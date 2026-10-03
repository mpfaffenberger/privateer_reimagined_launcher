//! Test store builder: writes the docs/DESIGN.md layout exactly like the publisher does.

#![allow(dead_code)]

use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use ed25519_dalek::{Signer, SigningKey};
use prl_core::format::{ChannelPointer, FileEntry, Manifest};
use sha2::{Digest, Sha256};

pub struct TestStore {
    pub dir: tempfile::TempDir,
    key: SigningKey,
}

impl TestStore {
    pub fn new() -> Self {
        TestStore { dir: tempfile::tempdir().unwrap(), key: SigningKey::from_bytes(&[7u8; 32]) }
    }

    pub fn root(&self) -> &Path {
        self.dir.path()
    }

    pub fn public_key(&self) -> String {
        B64.encode(self.key.verifying_key().to_bytes())
    }

    pub fn path(&self, key: &str) -> PathBuf {
        key.split('/').fold(self.root().to_path_buf(), |acc, s| acc.join(s))
    }

    fn write(&self, key: &str, data: &[u8]) {
        let p = self.path(key);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, data).unwrap();
    }

    /// Publish `files` as `version` on `stable`. Names ending in `.raw` are stored raw,
    /// everything else zstd, to exercise both encodings.
    pub fn publish(&self, version: &str, files: &[(&str, &[u8])]) -> Vec<u8> {
        let mut entries = Vec::new();
        for (path, data) in files {
            let sha = hex::encode(Sha256::digest(data));
            let (enc, stored) = if path.ends_with(".raw") {
                ("raw", data.to_vec())
            } else {
                ("zstd", zstd::encode_all(*data, 3).unwrap())
            };
            let entry = FileEntry { path: path.to_string(), size: data.len() as u64, sha256: sha,
                                    enc: enc.into(), stored_size: stored.len() as u64 };
            self.write(&entry.blob_key(), &stored);
            entries.push(entry);
        }
        entries.sort_by(|a, b| a.path.cmp(&b.path));
        let manifest = Manifest {
            format: 1, product: "privateer-reimagined".into(), version: version.into(),
            channel: "stable".into(), created_utc: "2026-10-03T00:00:00Z".into(),
            launch: [("windows".to_string(), "game.exe".to_string())].into_iter().collect(),
            total_size: entries.iter().map(|e| e.size).sum(), files: entries,
        };
        let bytes = serde_json::to_vec_pretty(&manifest).unwrap();
        let mkey = format!("manifests/{version}.json");
        self.write(&mkey, &bytes);
        self.write(&format!("{mkey}.sig"), B64.encode(self.key.sign(&bytes).to_bytes()).as_bytes());
        let pointer = ChannelPointer { format: 1, channel: "stable".into(), version: version.into(),
                                       manifest: mkey, manifest_sha256: hex::encode(Sha256::digest(&bytes)) };
        self.write("channels/stable.json", &serde_json::to_vec(&pointer).unwrap());
        bytes
    }
}
