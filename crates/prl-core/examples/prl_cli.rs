//! Headless driver for prl-core: manual testing, real-scale runs and support.
//!
//!   cargo run -p prl-core --release --example prl_cli -- detect
//!   cargo run -p prl-core --release --example prl_cli -- install <source> <install-dir> <pubkey> [--repair]
//!
//! <source> is `https://cdn.privateer-reimagined.com` or a local store folder.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use prl_core::install::{Installer, Mode};
use prl_core::ownership;
use prl_core::source::Source;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("detect") => {
            let candidates = ownership::candidates();
            println!("{} candidate folder(s):", candidates.len());
            for (store, path) in &candidates {
                let verdict = ownership::find_marker(path).map(|m| format!("OK ({})", m.display()));
                println!("  {store:?}  {}  -> {}", path.display(), verdict.unwrap_or_else(|| "no Privateer data".into()));
            }
            match ownership::detect() {
                Some(found) => println!("OWNED via {:?}: {}", found.store, found.path.display()),
                None => println!("NOT FOUND (the launcher would ask the player to pick the folder)"),
            }
        }
        Some("install") if args.len() >= 4 => {
            let mode = if args.iter().any(|a| a == "--repair") { Mode::Repair } else { Mode::Update };
            let inst = Installer::new(Source::from_spec(&args[1]), args[3].clone(), &args[2]);
            let t0 = Instant::now();
            let release = inst.check("stable").await.unwrap_or_else(|e| panic!("check: {e}"));
            println!("release {} ({} files, {:.2} GB installed)", release.manifest.version,
                     release.manifest.files.len(), release.manifest.total_size as f64 / 1e9);
            let plan = inst.plan(&release, mode).unwrap_or_else(|e| panic!("plan: {e}"));
            println!("plan in {:.1}s: {} blobs to download ({:.2} GB), {} files to write, {} to remove{}",
                     t0.elapsed().as_secs_f64(), plan.fetch.len(), plan.download_bytes as f64 / 1e9,
                     plan.place.len(), plan.remove.len(), if plan.up_to_date { " (up to date)" } else { "" });
            let last = Arc::new(AtomicU64::new(0));
            let t1 = Instant::now();
            inst.apply(&release, &plan, Arc::new(move |p| {
                let pct = if p.total_bytes == 0 { 100 } else { p.done_bytes * 100 / p.total_bytes };
                if pct >= last.load(Ordering::Relaxed) + 10 || p.phase == "done" {
                    last.store(pct, Ordering::Relaxed);
                    println!("  {:>8} {:>3}%  {}/{} files", p.phase, pct, p.files_done, p.files_total);
                }
            })).await.unwrap_or_else(|e| panic!("apply: {e}"));
            println!("applied in {:.1}s", t1.elapsed().as_secs_f64());
        }
        _ => eprintln!("usage: prl_cli detect | install <source> <install-dir> <pubkey> [--repair]"),
    }
}
