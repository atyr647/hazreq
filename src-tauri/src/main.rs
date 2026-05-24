// hazreq Tauri shell. Spawns the bundled Python runtime as a sidecar,
// waits for uvicorn to come up on a free port, then loads that URL in
// the main WebKitGTK window. On window close the Python process is
// terminated cleanly.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const PYTHON_REL: &str = "opt/python3.11/bin/python3.11";

struct BackendGuard(Mutex<Option<Child>>);

impl BackendGuard {
    fn new(child: Child) -> Self {
        Self(Mutex::new(Some(child)))
    }
    fn kill(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn pick_free_port() -> u16 {
    // Prefer 8000-8009 to keep the URL predictable; fall back to whatever
    // the kernel gives us if those are all taken.
    for p in 8000u16..8010 {
        if std::net::TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return p;
        }
    }
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("bind ephemeral");
    listener.local_addr().expect("local_addr").port()
}

fn runtime_dir() -> PathBuf {
    // Inside an AppImage, $APPDIR is the mounted squashfs root.
    if let Ok(appdir) = std::env::var("APPDIR") {
        let p = PathBuf::from(appdir).join("opt/hazreq/runtime");
        if p.join(PYTHON_REL).exists() {
            return p;
        }
    }
    // Dev fallback: walk up from the binary looking for a sibling runtime/.
    if let Ok(exe) = std::env::current_exe() {
        let mut here = exe.clone();
        while let Some(parent) = here.parent() {
            let candidate = parent.join("runtime");
            if candidate.join(PYTHON_REL).exists() {
                return candidate;
            }
            here = parent.to_path_buf();
        }
    }
    PathBuf::from("runtime")
}

fn data_dir() -> PathBuf {
    if let Ok(custom) = std::env::var("HAZREQ_DATA_DIR") {
        return PathBuf::from(custom);
    }
    let base = std::env::var("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(".local/share")
        });
    base.join("hazreq")
}

fn spawn_backend(runtime: &PathBuf, port: u16) -> Result<Child, String> {
    let python = runtime.join("opt/python3.11/bin/python3.11");
    let app_dir = runtime.join("opt/hazreq");
    let alembic_ini = app_dir.join("alembic.ini");
    let prep_script = app_dir.join("scripts/prepare_template.py");

    if !python.exists() {
        return Err(format!("bundled python missing at {}", python.display()));
    }

    let data = data_dir();
    let _ = std::fs::create_dir_all(data.join("templates"));
    let _ = std::fs::create_dir_all(data.join("pdfs"));
    let _ = std::fs::create_dir_all(data.join("backups"));
    let template_path = data.join("templates/hazmat_chit.docx");

    // Migrations first.
    let status = Command::new(&python)
        .args(["-m", "alembic", "-c"])
        .arg(&alembic_ini)
        .arg("upgrade")
        .arg("head")
        .env("HAZREQ_DATA_DIR", &data)
        .env("PYTHONPATH", &app_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .status()
        .map_err(|e| format!("alembic launch failed: {e}"))?;
    if !status.success() {
        return Err("alembic migration failed".into());
    }

    // Generate the docx template on first run.
    if !template_path.exists() {
        let _ = Command::new(&python)
            .arg(&prep_script)
            .env("HAZREQ_DATA_DIR", &data)
            .env("HAZREQ_TEMPLATE_PATH", &template_path)
            .env("PYTHONPATH", &app_dir)
            .status();
    }

    // Long-running uvicorn server.
    Command::new(&python)
        .args(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port"])
        .arg(port.to_string())
        .args(["--workers", "1"])
        .env("HAZREQ_DATA_DIR", &data)
        .env("HAZREQ_TEMPLATE_PATH", &template_path)
        .env("PYTHONPATH", &app_dir)
        .current_dir(&app_dir)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("uvicorn spawn failed: {e}"))
}

fn wait_for_port(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(75));
    }
    false
}

fn main() {
    let port = pick_free_port();

    tauri::Builder::default()
        .setup(move |app| {
            let runtime = runtime_dir();
            let child = spawn_backend(&runtime, port).map_err(|e| {
                eprintln!("hazreq: failed to start backend: {e}");
                e
            })?;
            app.manage(BackendGuard::new(child));

            let url = format!("http://127.0.0.1:{port}");
            let handle = app.handle().clone();
            thread::spawn(move || {
                if !wait_for_port(port, Duration::from_secs(30)) {
                    eprintln!("hazreq: backend never opened port {port}");
                    return;
                }
                let parsed = url.parse().expect("valid url");
                // Tauri 2 auto-creates a "main" window when none is configured;
                // if it exists, navigate it. Otherwise build a fresh one.
                if let Some(existing) = handle.get_webview_window("main") {
                    if let Err(e) = existing.eval(&format!("window.location.replace({:?})", url)) {
                        eprintln!("hazreq: navigate failed: {e}");
                    }
                    let _ = existing.set_title("hazreq");
                    let _ = existing.show();
                } else if let Err(e) = WebviewWindowBuilder::new(
                    &handle, "hazreq", WebviewUrl::External(parsed),
                )
                .title("hazreq")
                .inner_size(1200.0, 800.0)
                .visible(true)
                .build()
                {
                    eprintln!("hazreq: window build failed: {e}");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(guard) = app.try_state::<BackendGuard>() {
                    guard.kill();
                }
            }
        });
}
