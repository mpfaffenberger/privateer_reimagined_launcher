//! check -> plan -> apply against a real signed store on disk.

mod common;

use std::sync::Arc;

use common::TestStore;
use prl_core::install::{Installer, Mode};
use prl_core::source::Source;
use prl_core::Error;

fn installer(store: &TestStore, game: &std::path::Path) -> Installer {
    Installer::new(Source::Dir(store.root().to_path_buf()), store.public_key(), game)
}

async fn install(inst: &Installer, mode: Mode) -> prl_core::install::Plan {
    let release = inst.check("stable").await.unwrap();
    let plan = inst.plan(&release, mode).unwrap();
    inst.apply(&release, &plan, Arc::new(|_| {})).await.unwrap();
    plan
}

fn read(game: &std::path::Path, p: &str) -> Vec<u8> {
    std::fs::read(game.join(p)).unwrap()
}

#[tokio::test]
async fn fresh_install_then_up_to_date() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ v1"), ("assets/a.txt", b"alpha"), ("assets/n.raw", b"noise")]);
    let game = tempfile::tempdir().unwrap();
    let inst = installer(&store, game.path());

    let plan = install(&inst, Mode::Update).await;
    assert_eq!(plan.fetch.len(), 3);
    assert_eq!(read(game.path(), "assets/a.txt"), b"alpha");
    assert_eq!(read(game.path(), "assets/n.raw"), b"noise");
    assert_eq!(inst.installed().unwrap().version, "1.0.0");

    let release = inst.check("stable").await.unwrap();
    let again = inst.plan(&release, Mode::Update).unwrap();
    assert!(again.up_to_date && again.fetch.is_empty() && again.download_bytes == 0);
}

#[tokio::test]
async fn update_downloads_only_changes_and_never_touches_user_files() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ v1"), ("assets/keep.txt", b"same"), ("assets/old.txt", b"gone soon")]);
    let game = tempfile::tempdir().unwrap();
    let inst = installer(&store, game.path());
    install(&inst, Mode::Update).await;
    std::fs::create_dir_all(game.path().join("saves")).unwrap();
    std::fs::write(game.path().join("saves/slot1.sav"), b"my progress").unwrap();

    store.publish("1.0.1", &[("game.exe", b"MZ v2"), ("assets/keep.txt", b"same"), ("assets/new.txt", b"hello")]);
    let plan = install(&inst, Mode::Update).await;
    let fetched: Vec<_> = plan.fetch.iter().map(|f| f.path.as_str()).collect();
    assert_eq!(fetched.len(), 2, "only the changed exe and the new file: {fetched:?}");
    assert_eq!(plan.remove, vec!["assets/old.txt".to_string()]);
    assert_eq!(read(game.path(), "game.exe"), b"MZ v2");
    assert_eq!(read(game.path(), "assets/new.txt"), b"hello");
    assert!(!game.path().join("assets/old.txt").exists());
    assert_eq!(read(game.path(), "saves/slot1.sav"), b"my progress");
}

#[tokio::test]
async fn identical_content_is_downloaded_once_but_installed_everywhere() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ"), ("a/x.txt", b"dup"), ("b/y.txt", b"dup")]);
    let game = tempfile::tempdir().unwrap();
    let plan = install(&installer(&store, game.path()), Mode::Update).await;
    assert_eq!((plan.fetch.len(), plan.place.len()), (2, 3));
    assert_eq!(read(game.path(), "b/y.txt"), b"dup");
}

#[tokio::test]
async fn tampered_or_unsigned_releases_are_refused() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ")]);
    let game = tempfile::tempdir().unwrap();

    // A valid key, just not the release key.
    use base64::Engine;
    let wrong = base64::engine::general_purpose::STANDARD
        .encode(ed25519_dalek::SigningKey::from_bytes(&[9u8; 32]).verifying_key().to_bytes());
    let other = Installer::new(Source::Dir(store.root().into()), wrong, game.path());
    assert!(matches!(other.check("stable").await, Err(Error::Untrusted(_))));
    // No key compiled in: fail safe.
    let none = Installer::new(Source::Dir(store.root().into()), "", game.path());
    assert!(matches!(none.check("stable").await, Err(Error::Untrusted(_))));
    // Manifest edited after signing: the pointer hash catches it...
    let mpath = store.path("manifests/1.0.0.json");
    let original = std::fs::read(&mpath).unwrap();
    let edited = String::from_utf8(original.clone()).unwrap().replace("privateer-reimagined", "privateer-reimagineX");
    assert_ne!(edited.as_bytes(), &original[..]);
    std::fs::write(&mpath, &edited).unwrap();
    assert!(matches!(installer(&store, game.path()).check("stable").await, Err(Error::Untrusted(_))));
    // ...and even with the pointer re-pointed at the edit, the signature does.
    use sha2::Digest;
    let ppath = store.path("channels/stable.json");
    let mut pointer: serde_json::Value = serde_json::from_slice(&std::fs::read(&ppath).unwrap()).unwrap();
    pointer["manifest_sha256"] = hex::encode(sha2::Sha256::digest(edited.as_bytes())).into();
    std::fs::write(&ppath, serde_json::to_vec(&pointer).unwrap()).unwrap();
    match installer(&store, game.path()).check("stable").await {
        Err(Error::Untrusted(why)) => assert!(why.contains("signature"), "{why}"),
        other => panic!("expected a signature failure, got {other:?}"),
    }
}

#[tokio::test]
async fn a_corrupt_blob_never_replaces_installed_files() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ good")]);
    let game = tempfile::tempdir().unwrap();
    let inst = installer(&store, game.path());
    install(&inst, Mode::Update).await;

    store.publish("1.0.1", &[("game.exe", b"MZ newer")]);
    let release = inst.check("stable").await.unwrap();
    let plan = inst.plan(&release, Mode::Update).unwrap();
    std::fs::write(store.path(&plan.fetch[0].blob_key()), zstd::encode_all(&b"MZ evil!"[..], 3).unwrap()).unwrap();

    assert!(inst.apply(&release, &plan, Arc::new(|_| {})).await.is_err());
    assert_eq!(read(game.path(), "game.exe"), b"MZ good");
    assert_eq!(inst.installed().unwrap().version, "1.0.0");
}

#[tokio::test]
async fn repair_rehashes_while_update_trusts_the_cache() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", b"MZ"), ("assets/a.txt", b"alpha")]);
    let game = tempfile::tempdir().unwrap();
    let inst = installer(&store, game.path());
    install(&inst, Mode::Update).await;

    // Same-size corruption with the timestamp put back: only a full re-hash can see it.
    let target = game.path().join("assets/a.txt");
    let mtime = std::fs::metadata(&target).unwrap().modified().unwrap();
    std::fs::write(&target, b"ALPHA").unwrap();
    std::fs::File::options().write(true).open(&target).unwrap().set_modified(mtime).unwrap();

    let release = inst.check("stable").await.unwrap();
    assert!(inst.plan(&release, Mode::Update).unwrap().up_to_date);
    let plan = install(&inst, Mode::Repair).await;
    assert_eq!(plan.place.len(), 1);
    assert_eq!(read(game.path(), "assets/a.txt"), b"alpha");

    // A normal edit changes the timestamp, so even Update mode catches it.
    std::fs::write(&target, b"edit!").unwrap();
    assert_eq!(inst.plan(&release, Mode::Update).unwrap().place.len(), 1);
}

#[tokio::test]
async fn progress_reports_reach_done() {
    let store = TestStore::new();
    store.publish("1.0.0", &[("game.exe", &[1u8; 300_000]), ("assets/b.raw", &[2u8; 100_000])]);
    let game = tempfile::tempdir().unwrap();
    let inst = installer(&store, game.path());
    let release = inst.check("stable").await.unwrap();
    let plan = inst.plan(&release, Mode::Update).unwrap();
    let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
    let s2 = seen.clone();
    inst.apply(&release, &plan, Arc::new(move |p| s2.lock().unwrap().push((p.phase, p.done_bytes, p.total_bytes))))
        .await
        .unwrap();
    let seen = seen.lock().unwrap();
    let last = seen.last().unwrap();
    assert_eq!(last.0, "done");
    assert_eq!(last.1, plan.download_bytes);
    assert!(seen.windows(2).all(|w| w[0].0 != "download" || w[1].0 != "download" || w[0].1 <= w[1].1));
}
