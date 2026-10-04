//! check -> plan -> apply. See docs/DESIGN.md ("Launcher verification order", "Install
//! layout"). Nothing replaces a file until its content has been verified, and only files a
//! previous release installed are ever deleted, so saves and settings are safe.

use std::collections::{HashMap, HashSet};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use futures_util::{stream, StreamExt, TryStreamExt};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::format::{self, ChannelPointer, FileEntry, Manifest, FORMAT};
use crate::source::Source;
use crate::{io_err, verify, Error, Result};

pub const STATE_DIR: &str = ".prl";
const PARALLEL_DOWNLOADS: usize = 6;

/// A release whose pointer, hash and signature all checked out.
#[derive(Debug, Clone)]
pub struct Release {
    pub pointer: ChannelPointer,
    pub manifest: Manifest,
    pub manifest_bytes: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Trust the hash cache for files whose size + mtime are unchanged.
    Update,
    /// Re-hash every file (the "Verify files" button).
    Repair,
}

#[derive(Debug, Clone, Serialize)]
pub struct Plan {
    pub version: String,
    /// Unique blobs to download (one per distinct content).
    pub fetch: Vec<FileEntry>,
    /// Every install path to (re)write.
    pub place: Vec<FileEntry>,
    /// Paths the old release installed that the new one doesn't have.
    pub remove: Vec<String>,
    pub download_bytes: u64,
    pub up_to_date: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub phase: &'static str,
    pub done_bytes: u64,
    pub total_bytes: u64,
    pub files_done: usize,
    pub files_total: usize,
}

pub type ProgressFn = Arc<dyn Fn(Progress) + Send + Sync>;

#[derive(Default, Serialize, Deserialize)]
struct HashCache {
    entries: HashMap<String, CacheEntry>,
}

#[derive(Clone, Serialize, Deserialize)]
struct CacheEntry {
    size: u64,
    mtime_ns: u128,
    sha256: String,
}

pub struct Installer {
    source: Source,
    public_key: String,
    root: PathBuf,
}

fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

fn hash_file(path: &Path) -> Result<String> {
    let mut file = std::fs::File::open(path).map_err(io_err(path))?;
    let mut hasher = Sha256::new();
    std::io::copy(&mut file, &mut hasher).map_err(io_err(path))?;
    Ok(hex::encode(hasher.finalize()))
}

/// Hash many files on a bounded pool of scoped threads (hashing is I/O + CPU bound and
/// parallelises well: "Verify files" on the full game went from ~3 min single-threaded).
fn hash_files(paths: &[PathBuf]) -> Vec<Result<String>> {
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4).clamp(1, 8);
    let next = AtomicUsize::new(0);
    let slots: Vec<std::sync::Mutex<Option<Result<String>>>> = paths.iter().map(|_| std::sync::Mutex::new(None)).collect();
    std::thread::scope(|s| {
        for _ in 0..workers.min(paths.len().max(1)) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= paths.len() {
                    break;
                }
                *slots[i].lock().unwrap() = Some(hash_file(&paths[i]));
            });
        }
    });
    slots.into_iter().map(|m| m.into_inner().unwrap().expect("every slot hashed")).collect()
}

fn mtime_ns(md: &std::fs::Metadata) -> u128 {
    md.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_nanos()).unwrap_or(0)
}

/// Writer that hashes and counts everything passing through it.
struct HashingWriter<W: Write> {
    inner: W,
    hasher: Sha256,
    written: u64,
}

impl<W: Write> Write for HashingWriter<W> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let n = self.inner.write(buf)?;
        self.hasher.update(&buf[..n]);
        self.written += n as u64;
        Ok(n)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

/// Decode a downloaded blob into `dst`, returning (sha256, size) of the decoded content.
fn decode_and_hash(src: &Path, dst: &Path, enc: &str) -> Result<(String, u64)> {
    let input = std::fs::File::open(src).map_err(io_err(src))?;
    let out = std::fs::File::create(dst).map_err(io_err(dst))?;
    let mut w = HashingWriter { inner: BufWriter::new(out), hasher: Sha256::new(), written: 0 };
    let res = if enc == "zstd" {
        zstd::stream::copy_decode(input, &mut w)
    } else {
        std::io::copy(&mut std::io::BufReader::new(input), &mut w).map(|_| ())
    };
    res.map_err(|e| Error::Format(format!("corrupt download {}: {e}", src.display())))?;
    w.flush().map_err(io_err(dst))?;
    Ok((hex::encode(w.hasher.finalize()), w.written))
}

/// Copy a verified blob next to `target`, then atomically rename it into place.
fn place_file(blob: &Path, target: &Path) -> Result<()> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(io_err(parent))?;
    }
    let name = target.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let tmp = target.with_file_name(format!(".{name}.prl-tmp"));
    std::fs::copy(blob, &tmp).map_err(io_err(&tmp))?;
    std::fs::rename(&tmp, target).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        Error::Io { path: target.display().to_string(), source: e }
    })
}

impl Installer {
    pub fn new(source: Source, public_key: impl Into<String>, root: impl Into<PathBuf>) -> Self {
        Installer { source, public_key: public_key.into(), root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn state(&self) -> PathBuf {
        self.root.join(STATE_DIR)
    }

    /// Fetch and verify the channel's current release (docs/DESIGN.md order).
    pub async fn check(&self, channel: &str) -> Result<Release> {
        if channel.is_empty() || !channel.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
            return Err(Error::Format(format!("bad channel name {channel:?}")));
        }
        let pointer: ChannelPointer = serde_json::from_slice(&self.source.get(&format!("channels/{channel}.json")).await?)
            .map_err(|e| Error::Format(format!("channel pointer: {e}")))?;
        let untrusted = |why: &str| Err(Error::Untrusted(why.to_string()));
        if pointer.format != FORMAT || pointer.channel != channel {
            return untrusted("channel pointer does not match the requested channel");
        }
        format::check_path(&pointer.version)?;
        if pointer.version.contains('/') || pointer.manifest != format!("manifests/{}.json", pointer.version) {
            return untrusted("channel pointer names an unexpected manifest");
        }
        let bytes = self.source.get(&pointer.manifest).await?;
        if sha256_hex(&bytes) != pointer.manifest_sha256 {
            return untrusted("manifest does not match the channel pointer's hash");
        }
        let sig = self.source.get(&format!("{}.sig", pointer.manifest)).await?;
        verify::verify_manifest(&self.public_key, &bytes, &sig)?;
        let manifest = Manifest::parse(&bytes)?;
        if manifest.channel != channel || manifest.version != pointer.version {
            return untrusted("manifest channel/version disagree with the pointer");
        }
        Ok(Release { pointer, manifest, manifest_bytes: bytes })
    }

    /// The release currently installed here, if any.
    pub fn installed(&self) -> Option<Manifest> {
        let bytes = std::fs::read(self.state().join("installed.json")).ok()?;
        Manifest::parse(&bytes).ok()
    }

    fn load_cache(&self) -> HashCache {
        std::fs::read(self.state().join("hashcache.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default()
    }

    fn save_cache(&self, cache: &HashCache) -> Result<()> {
        let dir = self.state();
        std::fs::create_dir_all(&dir).map_err(io_err(&dir))?;
        let path = dir.join("hashcache.json");
        std::fs::write(&path, serde_json::to_vec(cache).unwrap_or_default()).map_err(io_err(&path))
    }

    /// Decide what to download/write/delete. Blocking (it may hash files): call it from a
    /// worker thread in UI code.
    pub fn plan(&self, release: &Release, mode: Mode) -> Result<Plan> {
        let mut cache = if mode == Mode::Repair { HashCache::default() } else { self.load_cache() };
        let mut place = Vec::new();
        // Pass 1 (cheap): missing / wrong size -> rewrite; cache hit -> compare; else hash later.
        let mut to_hash: Vec<(&FileEntry, PathBuf, u128)> = Vec::new();
        for f in &release.manifest.files {
            let path = format::install_path(&self.root, &f.path);
            match std::fs::metadata(&path) {
                Ok(md) if md.is_file() && md.len() == f.size => {
                    let m = mtime_ns(&md);
                    match cache.entries.get(&f.path) {
                        Some(c) if c.size == f.size && c.mtime_ns == m => {
                            if c.sha256 != f.sha256 {
                                place.push(f.clone());
                            }
                        }
                        _ => to_hash.push((f, path, m)),
                    }
                }
                _ => place.push(f.clone()),
            }
        }
        // Pass 2: hash everything uncached in parallel.
        let paths: Vec<PathBuf> = to_hash.iter().map(|(_, p, _)| p.clone()).collect();
        for ((f, _, m), sha) in to_hash.iter().zip(hash_files(&paths)) {
            let sha = sha?;
            if sha != f.sha256 {
                place.push((*f).clone());
            }
            cache.entries.insert(f.path.clone(), CacheEntry { size: f.size, mtime_ns: *m, sha256: sha });
        }
        self.save_cache(&cache)?;

        let mut seen = HashSet::new();
        let fetch: Vec<FileEntry> = place.iter().filter(|f| seen.insert(f.sha256.clone())).cloned().collect();
        let wanted: HashSet<String> = release.manifest.files.iter().map(|f| f.path.to_lowercase()).collect();
        let installed = self.installed();
        let remove: Vec<String> = installed
            .as_ref()
            .map(|old| {
                old.files
                    .iter()
                    .filter(|f| !wanted.contains(&f.path.to_lowercase()))
                    .filter(|f| format::install_path(&self.root, &f.path).is_file())
                    .map(|f| f.path.clone())
                    .collect()
            })
            .unwrap_or_default();
        let same_version = installed.map(|m| m.version == release.manifest.version).unwrap_or(false);
        Ok(Plan {
            version: release.manifest.version.clone(),
            download_bytes: fetch.iter().map(|f| f.stored_size).sum(),
            up_to_date: place.is_empty() && remove.is_empty() && same_version,
            fetch,
            place,
            remove,
        })
    }

    async fn fetch_blob(&self, f: &FileEntry, staging: &Path, done: &AtomicU64, report: &(dyn Fn() + Send + Sync)) -> Result<()> {
        let ready = staging.join(&f.sha256);
        if std::fs::metadata(&ready).map(|m| m.len() == f.size).unwrap_or(false) {
            done.fetch_add(f.stored_size, Ordering::Relaxed); // verified in an earlier run
            report();
            return Ok(());
        }
        let part = staging.join(format!("{}.part", f.sha256));
        self.source
            .download(&f.blob_key(), &part, &|n| {
                done.fetch_add(n, Ordering::Relaxed);
                report();
            })
            .await?;
        let decoded = staging.join(format!("{}.decoded", f.sha256));
        let (enc, p2, d2) = (f.enc.clone(), part.clone(), decoded.clone());
        let (sha, size) = tokio::task::spawn_blocking(move || decode_and_hash(&p2, &d2, &enc))
            .await
            .map_err(|e| Error::Fetch(e.to_string()))??;
        let _ = std::fs::remove_file(&part);
        if sha != f.sha256 || size != f.size {
            let _ = std::fs::remove_file(&decoded);
            return Err(Error::Untrusted(format!("downloaded content for {} failed verification", f.path)));
        }
        std::fs::rename(&decoded, &ready).map_err(io_err(&ready))
    }

    /// Download, verify and install a plan. On error nothing half-written is left in the
    /// install: verified blobs stay in `.prl/staging` and are reused by the next attempt.
    pub async fn apply(&self, release: &Release, plan: &Plan, progress: ProgressFn) -> Result<()> {
        let staging = self.state().join("staging");
        std::fs::create_dir_all(&staging).map_err(io_err(&staging))?;
        let total = plan.download_bytes;
        let done = AtomicU64::new(0);
        let blobs_done = AtomicUsize::new(0);
        let report = || {
            progress(Progress {
                phase: "download",
                done_bytes: done.load(Ordering::Relaxed).min(total),
                total_bytes: total,
                files_done: blobs_done.load(Ordering::Relaxed),
                files_total: plan.fetch.len(),
            })
        };
        report();
        // Stream OWNED entries: a closure over `&FileEntry` returning a future is higher-ranked
        // over that borrow, and rustc can't prove such futures `Send` (which Tauri requires).
        let (staging_ref, done_ref, blobs_ref, report_ref) = (&staging, &done, &blobs_done, &report);
        stream::iter(plan.fetch.clone())
            .map(|f| async move {
                self.fetch_blob(&f, staging_ref, done_ref, report_ref).await?;
                blobs_ref.fetch_add(1, Ordering::Relaxed);
                report_ref();
                Ok::<(), Error>(())
            })
            .buffer_unordered(PARALLEL_DOWNLOADS)
            .try_collect::<Vec<()>>()
            .await?;

        // Every needed blob is now verified on disk; install them.
        let mut cache = self.load_cache();
        for (i, f) in plan.place.iter().enumerate() {
            let target = format::install_path(&self.root, &f.path);
            place_file(&staging.join(&f.sha256), &target)?;
            let md = std::fs::metadata(&target).map_err(io_err(&target))?;
            cache.entries.insert(f.path.clone(), CacheEntry { size: f.size, mtime_ns: mtime_ns(&md), sha256: f.sha256.clone() });
            progress(Progress { phase: "install", done_bytes: total, total_bytes: total, files_done: i + 1, files_total: plan.place.len() });
        }
        for path in &plan.remove {
            let target = format::install_path(&self.root, path);
            if target.is_file() {
                std::fs::remove_file(&target).map_err(io_err(&target))?;
            }
            cache.entries.remove(path);
        }
        self.save_cache(&cache)?;
        let installed = self.state().join("installed.json");
        std::fs::write(&installed, &release.manifest_bytes).map_err(io_err(&installed))?;
        let _ = std::fs::remove_dir_all(&staging);
        progress(Progress { phase: "done", done_bytes: total, total_bytes: total, files_done: plan.place.len(), files_total: plan.place.len() });
        Ok(())
    }

    /// Start the installed game with the install folder as its working directory.
    pub fn launch(&self) -> Result<std::process::Child> {
        let manifest = self.installed().ok_or_else(|| Error::Format("the game is not installed".into()))?;
        let exe = manifest.launch_target().ok_or_else(|| Error::Format("this release has no launcher entry for this OS".into()))?;
        let path = format::install_path(&self.root, exe);
        make_executable(&path)?;
        std::process::Command::new(&path).current_dir(&self.root).spawn().map_err(io_err(&path))
    }
}

/// Installed files are written without an execute bit, which Windows ignores but macOS and
/// Linux won't run (#66). Set it on the launch target right before starting it, so a repair or
/// any other way the file was (re)placed is covered too.
#[cfg(unix)]
fn make_executable(path: &std::path::Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = std::fs::metadata(path).map_err(io_err(path))?.permissions();
    if perms.mode() & 0o111 != 0o111 {
        perms.set_mode(perms.mode() | 0o755);
        std::fs::set_permissions(path, perms).map_err(io_err(path))?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn make_executable(_path: &std::path::Path) -> Result<()> {
    Ok(())
}

#[cfg(all(test, unix))]
mod unix_tests {
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn launch_target_becomes_executable() {
        let dir = tempfile::tempdir().unwrap();
        let exe = dir.path().join("game");
        std::fs::write(&exe, b"#!/bin/sh\n").unwrap();
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o644)).unwrap();
        super::make_executable(&exe).unwrap();
        assert_eq!(std::fs::metadata(&exe).unwrap().permissions().mode() & 0o777, 0o755);
    }
}
