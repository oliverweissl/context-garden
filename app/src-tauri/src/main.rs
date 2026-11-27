// No console window next to the app on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Agent terminals of the network security layer start through the app
    // binary itself (the sandbox helper, #81).
    #[cfg(target_os = "linux")]
    if let Some(code) = cg_app_lib::sandbox::helper() {
        std::process::exit(code);
    }
    cg_app_lib::run()
}
