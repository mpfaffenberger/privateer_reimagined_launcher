// No console window behind the launcher in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    privateer_launcher_lib::run()
}
