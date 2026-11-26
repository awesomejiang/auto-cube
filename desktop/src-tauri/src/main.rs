#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    collections::VecDeque,
    fs,
    io::{BufRead, BufReader, Read, Write},
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::Manager;

#[derive(Clone, Serialize, Default)]
struct Report {
    running: bool,
    error: bool,
    lines: VecDeque<String>,
}

#[derive(Default)]
struct Session {
    child: Option<Child>,
    report: Report,
    stopped: bool,
}

type Shared = Arc<Mutex<Session>>;

fn append(report: &mut Report, message: String) {
    if report.lines.len() == 200 {
        report.lines.pop_front();
    }
    report.lines.push_back(message);
}

fn read_output(stream: impl Read + Send + 'static, state: Shared) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            append(&mut state.lock().unwrap().report, line);
        }
    })
}

fn package_dir() -> Result<PathBuf, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let directory = executable.parent().ok_or("找不到程序目录")?;
    if directory.join("runtime/python.exe").is_file() && directory.join("quanah_cube.py").is_file()
    {
        return Ok(directory.to_path_buf());
    }
    #[cfg(debug_assertions)]
    {
        let directory = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../dist/Quanah-Cube");
        if directory.join("runtime/python.exe").is_file()
            && directory.join("quanah_cube.py").is_file()
        {
            return Ok(directory);
        }
    }
    Err("找不到 Python 运行时。请解压完整的 Windows 便携包。".to_owned())
}

#[tauri::command]
fn start_run(
    mode: String,
    targets: [String; 3],
    read_cp: bool,
    state: tauri::State<'_, Shared>,
) -> Result<(), String> {
    let directory = package_dir()?;
    let mut command = Command::new(directory.join("runtime/python.exe"));
    command
        .arg(directory.join("quanah_cube.py"))
        .arg("--switch-now")
        .current_dir(&directory)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(0x08000000); // CREATE_NO_WINDOW

    match mode.as_str() {
        "preview" => {
            command.arg("--preview");
        }
        "test" => {
            command.arg("--preview").arg("--target").args(targets);
        }
        "potential" => {
            command.arg("--target").args(targets);
        }
        "either" => {
            command.arg("--also-cp").arg("--target").args(targets);
        }
        "cp" => {}
        _ => return Err("无效的运行模式".to_owned()),
    }
    if matches!(mode.as_str(), "preview" | "test") {
        command.arg(if read_cp { "--also-cp" } else { "--skip-cp" });
    }

    let mut session = state.lock().unwrap();
    if session.report.running {
        return Err("已有任务正在运行".to_owned());
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动 OCR：{error}"))?;
    let stdout = read_output(child.stdout.take().unwrap(), state.inner().clone());
    let stderr = read_output(child.stderr.take().unwrap(), state.inner().clone());
    session.child = Some(child);
    session.report = Report {
        running: true,
        ..Report::default()
    };
    session.stopped = false;

    let shared = state.inner().clone();
    thread::spawn(move || {
        let outcome = loop {
            let status = shared.lock().unwrap().child.as_mut().unwrap().try_wait();
            match status {
                Ok(None) => thread::sleep(Duration::from_millis(100)),
                result => break result,
            }
        };
        let _ = stdout.join();
        let _ = stderr.join();
        let mut session = shared.lock().unwrap();
        session.child = None;
        session.report.running = false;
        if !session.stopped && !matches!(outcome, Ok(Some(status)) if status.success()) {
            session.report.error = true;
            append(&mut session.report, "OCR 进程异常退出。".to_owned());
        }
    });

    Ok(())
}

fn stop(state: &Shared) {
    let mut session = state.lock().unwrap();
    if let Some(child) = session.child.as_mut() {
        if let Some(mut input) = child.stdin.take() {
            let _ = input.write_all(b"\n");
            session.stopped = true;
        }
    }
}

#[tauri::command]
fn stop_run(state: tauri::State<'_, Shared>) {
    stop(state.inner());
}

#[tauri::command]
fn get_report(state: tauri::State<'_, Shared>) -> Report {
    state.lock().unwrap().report.clone()
}

fn window_size_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    Some(app.path().app_config_dir().ok()?.join("window-size.txt"))
}

fn restore_window_size(window: &tauri::WebviewWindow) {
    let Some(path) = window_size_path(window.app_handle()) else {
        return;
    };
    let Ok(saved) = fs::read_to_string(path) else {
        return;
    };
    let Some((width, height)) = saved.trim().split_once(' ') else {
        return;
    };
    let (Ok(width), Ok(height)) = (width.parse::<u32>(), height.parse::<u32>()) else {
        return;
    };
    if width > 0 && height > 0 {
        let _ = window.set_size(tauri::LogicalSize::new(width, height));
    }
}

fn save_window_size(window: &tauri::Window) {
    if window.is_minimized().unwrap_or(true) || window.is_maximized().unwrap_or(true) {
        return;
    }
    let (Ok(size), Ok(scale)) = (window.inner_size(), window.scale_factor()) else {
        return;
    };
    let Some(path) = window_size_path(window.app_handle()) else {
        return;
    };
    let size = size.to_logical::<u32>(scale);
    if let Some(directory) = path.parent() {
        if fs::create_dir_all(directory).is_ok() {
            let _ = fs::write(path, format!("{} {}\n", size.width, size.height));
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Shared::default())
        .invoke_handler(tauri::generate_handler![start_run, stop_run, get_report])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                restore_window_size(&window);
            }
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { .. } => save_window_size(window),
            tauri::WindowEvent::Destroyed => stop(window.state::<Shared>().inner()),
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("无法启动 Quanah Cube");
}
