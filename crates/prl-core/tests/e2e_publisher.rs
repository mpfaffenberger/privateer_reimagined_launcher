//! Cross-language contract test: the REAL Python publisher builds + publishes, the Rust
//! core installs + updates from it. Skips (with a note) when `uv` isn't on PATH.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

use prl_core::install::{Installer, Mode};
use prl_core::source::Source;

fn publisher_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("publisher")
}

fn prl(args: &[&str]) -> String {
    let out = Command::new("uv").args(["run", "--quiet", "prl"]).args(args)
        .current_dir(publisher_dir()).output().expect("run uv");
    let text = String::from_utf8_lossy(&out.stdout).to_string() + &String::from_utf8_lossy(&out.stderr);
    assert!(out.status.success(), "prl {args:?} failed:\n{text}");
    text
}

#[tokio::test]
async fn rust_core_installs_what_the_python_publisher_publishes() {
    if Command::new("uv").arg("--version").output().is_err() {
        eprintln!("SKIP: uv not found");
        return;
    }
    let t = tempfile::tempdir().unwrap();
    let game = t.path().join("game");
    std::fs::create_dir_all(game.join("assets/data")).unwrap();
    std::fs::write(game.join("new_privateer.exe"), b"MZ release one").unwrap();
    std::fs::write(game.join("assets/data/ships.json"), "{\"tarsus\": 1}\n".repeat(300)).unwrap();
    std::fs::write(game.join("assets/model.blend"), b"never shipped").unwrap();
    let cfg = t.path().join("release.toml");
    let posix = |p: &Path| p.to_string_lossy().replace('\\', "/");
    std::fs::write(&cfg, format!(
        "product = \"privateer-reimagined\"\n[launch]\nwindows = \"new_privateer.exe\"\n\
         [[source]]\nfrom = \"{}\"\nto = \"new_privateer.exe\"\n\
         [[source]]\nfrom = \"{}\"\nto = \"assets\"\nexclude = [\"**/*.blend\"]\n",
        posix(&game.join("new_privateer.exe")), posix(&game.join("assets")))).unwrap();

    let key = t.path().join("key.pem");
    let keygen = prl(&["keygen", "--out", &key.to_string_lossy()]);
    let pubkey = keygen.lines().find_map(|l| l.split("keys.rs): ").nth(1)).expect("public key line").trim().to_string();
    let local = t.path().join("local_store");
    let remote = t.path().join("remote");
    let s = |p: &Path| p.to_string_lossy().to_string();
    prl(&["build", "--config", &s(&cfg), "--version", "1.0.0", "--store", &s(&local), "--key", &s(&key)]);
    prl(&["publish", "--version", "1.0.0", "--from", &s(&local), "--to", &s(&remote)]);

    let install_dir = t.path().join("install");
    let inst = Installer::new(Source::Dir(remote.clone()), pubkey.clone(), &install_dir);
    let release = inst.check("stable").await.expect("rust verifies python's signature + formats");
    let plan = inst.plan(&release, Mode::Update).unwrap();
    inst.apply(&release, &plan, Arc::new(|_| {})).await.unwrap();
    assert_eq!(std::fs::read(install_dir.join("new_privateer.exe")).unwrap(), b"MZ release one");
    assert_eq!(std::fs::read(install_dir.join("assets/data/ships.json")).unwrap(),
               std::fs::read(game.join("assets/data/ships.json")).unwrap());
    assert!(!install_dir.join("assets/model.blend").exists());

    // Release two changes only the exe: exactly one blob travels.
    std::fs::write(game.join("new_privateer.exe"), b"MZ release two").unwrap();
    prl(&["build", "--config", &s(&cfg), "--version", "1.0.1", "--store", &s(&local), "--key", &s(&key)]);
    prl(&["publish", "--version", "1.0.1", "--from", &s(&local), "--to", &s(&remote)]);
    let release = inst.check("stable").await.unwrap();
    assert_eq!(release.manifest.version, "1.0.1");
    let plan = inst.plan(&release, Mode::Update).unwrap();
    assert_eq!(plan.fetch.len(), 1);
    inst.apply(&release, &plan, Arc::new(|_| {})).await.unwrap();
    assert_eq!(std::fs::read(install_dir.join("new_privateer.exe")).unwrap(), b"MZ release two");
    assert!(inst.plan(&release, Mode::Repair).unwrap().up_to_date);
}
