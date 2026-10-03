//! Where releases come from: the CDN over HTTPS, or a local store directory (tests,
//! offline mirrors). Both use the docs/DESIGN.md store layout.

use std::path::{Path, PathBuf};

use futures_util::StreamExt;
use tokio::io::AsyncWriteExt;

use crate::{io_err, Error, Result};

#[derive(Clone)]
pub enum Source {
    Http { client: reqwest::Client, base: String },
    Dir(PathBuf),
}

impl Source {
    /// `https://cdn...` (or `http://` for local testing) -> HTTP; anything else is a folder.
    pub fn from_spec(spec: &str) -> Source {
        if spec.starts_with("https://") || spec.starts_with("http://") {
            let client = reqwest::Client::builder()
                .user_agent(concat!("privateer-reimagined-launcher/", env!("CARGO_PKG_VERSION")))
                .build()
                .expect("http client");
            Source::Http { client, base: spec.trim_end_matches('/').to_string() }
        } else {
            Source::Dir(PathBuf::from(spec.trim_start_matches("file://")))
        }
    }

    fn dir_path(root: &Path, key: &str) -> PathBuf {
        key.split('/').fold(root.to_path_buf(), |acc, seg| acc.join(seg))
    }

    /// Small objects: channel pointers, manifests, signatures.
    pub async fn get(&self, key: &str) -> Result<Vec<u8>> {
        match self {
            Source::Http { client, base } => {
                let resp = client.get(format!("{base}/{key}")).send().await
                    .map_err(|e| Error::Fetch(format!("{key}: {e}")))?;
                if !resp.status().is_success() {
                    return Err(Error::Fetch(format!("{key}: HTTP {}", resp.status())));
                }
                Ok(resp.bytes().await.map_err(|e| Error::Fetch(format!("{key}: {e}")))?.to_vec())
            }
            Source::Dir(root) => {
                let p = Self::dir_path(root, key);
                tokio::fs::read(&p).await.map_err(|e| Error::Fetch(format!("{}: {e}", p.display())))
            }
        }
    }

    /// Stream a blob to `dest`, reporting bytes as they arrive. Returns bytes written.
    pub async fn download(&self, key: &str, dest: &Path, on_bytes: &(dyn Fn(u64) + Send + Sync)) -> Result<u64> {
        let mut out = tokio::fs::File::create(dest).await.map_err(io_err(dest))?;
        let mut total = 0u64;
        match self {
            Source::Http { client, base } => {
                let resp = client.get(format!("{base}/{key}")).send().await
                    .map_err(|e| Error::Fetch(format!("{key}: {e}")))?;
                if !resp.status().is_success() {
                    return Err(Error::Fetch(format!("{key}: HTTP {}", resp.status())));
                }
                let mut stream = resp.bytes_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|e| Error::Fetch(format!("{key}: {e}")))?;
                    out.write_all(&chunk).await.map_err(io_err(dest))?;
                    total += chunk.len() as u64;
                    on_bytes(chunk.len() as u64);
                }
            }
            Source::Dir(root) => {
                let src = Self::dir_path(root, key);
                let mut input = tokio::fs::File::open(&src).await.map_err(|e| Error::Fetch(format!("{}: {e}", src.display())))?;
                let mut buf = vec![0u8; 1 << 20];
                loop {
                    let n = tokio::io::AsyncReadExt::read(&mut input, &mut buf).await.map_err(io_err(&src))?;
                    if n == 0 {
                        break;
                    }
                    out.write_all(&buf[..n]).await.map_err(io_err(dest))?;
                    total += n as u64;
                    on_bytes(n as u64);
                }
            }
        }
        out.flush().await.map_err(io_err(dest))?;
        Ok(total)
    }
}
