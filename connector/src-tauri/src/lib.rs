use reqwest::Client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs,
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Arc,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, State, WindowEvent,
};
#[cfg(target_os = "macos")]
use tauri_plugin_autostart::MacosLauncher;
use tokio::sync::{Mutex, RwLock};
use url::Url;
use uuid::Uuid;
use wait_timeout::ChildExt;

const KEYRING_SERVICE: &str = "MathPrint Connector";
const MAX_PDF_BYTES: usize = 200 * 1024 * 1024;

fn autostart_plugin<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    #[cfg(target_os = "macos")]
    {
        return tauri_plugin_autostart::Builder::new()
            .macos_launcher(MacosLauncher::LaunchAgent)
            .build();
    }

    #[cfg(not(target_os = "macos"))]
    tauri_plugin_autostart::Builder::new().build()
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct StoredConfig {
    server_url: String,
    email: String,
    installation_id: String,
    device_name: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct JobView {
    id: String,
    title: String,
    printer: String,
    pass_side: String,
    status: String,
    error: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize)]
struct ConnectorStateView {
    connected: bool,
    email: String,
    server_url: String,
    device_name: String,
    status: String,
    printer_count: usize,
    current_job: Option<String>,
    jobs: Vec<JobView>,
}

struct RuntimeState {
    view: RwLock<ConnectorStateView>,
    config: RwLock<StoredConfig>,
    token: RwLock<Option<String>>,
    paused: RwLock<bool>,
    job_gate: Mutex<()>,
    client: Client,
    config_path: PathBuf,
    journal_path: PathBuf,
    cache_dir: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct LocalPrinter {
    name: String,
    is_default: bool,
}

#[derive(Serialize)]
struct LoginRequest<'a> {
    email: &'a str,
    password: &'a str,
    installation_id: &'a str,
    device_name: &'a str,
    platform: &'static str,
    arch: &'static str,
    app_version: &'static str,
}

#[derive(Deserialize)]
struct LoginResponse {
    token: String,
}

#[derive(Serialize)]
struct HeartbeatRequest {
    app_version: &'static str,
    printers: Vec<LocalPrinter>,
}

#[derive(Clone, Debug, Deserialize)]
struct PrintOptions {
    copies: u32,
    duplex: bool,
    media: String,
    scale: String,
    collate: bool,
    reverse_applied_to_pdf: bool,
}

#[derive(Clone, Debug, Deserialize)]
struct RemoteJob {
    id: String,
    title: String,
    printer: String,
    pass_side: String,
    status: String,
    options: PrintOptions,
    sha256: String,
    size: usize,
    download_url: String,
}

#[derive(Deserialize)]
struct ClaimResponse {
    job: Option<RemoteJob>,
}

#[derive(Serialize)]
struct JobResultRequest<'a> {
    status: &'a str,
    spool_job_id: &'a str,
    error: &'a str,
}

fn platform_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "unsupported"
    }
}

fn arch_name() -> &'static str {
    if cfg!(target_arch = "aarch64") {
        "aarch64"
    } else if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else {
        "unknown"
    }
}

fn api_url(base: &str, path: &str) -> String {
    format!("{}{}", base.trim_end_matches('/'), path)
}

fn validate_server_url(raw: &str) -> Result<String, String> {
    let normalized = raw.trim().trim_end_matches('/').to_string();
    let parsed = Url::parse(&normalized).map_err(|_| "Adresse MathPrint invalide".to_string())?;
    let host = parsed.host_str().unwrap_or_default();
    let local = matches!(host, "localhost" | "127.0.0.1" | "::1");
    if parsed.scheme() != "https" && !(local && parsed.scheme() == "http") {
        return Err(
            "MathPrint doit utiliser HTTPS (HTTP n'est permis qu'en développement local)".into(),
        );
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err("L'adresse MathPrint ne doit contenir ni paramètres ni fragment".into());
    }
    Ok(normalized)
}

fn load_config(path: &Path) -> StoredConfig {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn atomic_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let temp = path.with_extension("tmp");
    fs::write(
        &temp,
        serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    atomic_replace(&temp, path)
}

#[cfg(not(target_os = "windows"))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    fs::rename(source, destination).map_err(|e| e.to_string())
}

#[cfg(target_os = "windows")]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    // SAFETY: buffers UTF-16 terminés par NUL, valides pendant tout l'appel.
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error().to_string())
    } else {
        Ok(())
    }
}

fn token_entry(installation_id: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, installation_id).map_err(|e| e.to_string())
}

fn save_token(installation_id: &str, token: &str) -> Result<(), String> {
    token_entry(installation_id)?
        .set_password(token)
        .map_err(|e| e.to_string())
}

fn read_token(installation_id: &str) -> Option<String> {
    if installation_id.is_empty() {
        return None;
    }
    token_entry(installation_id).ok()?.get_password().ok()
}

fn delete_token(installation_id: &str) {
    if let Ok(entry) = token_entry(installation_id) {
        let _ = entry.delete_credential();
    }
}

async fn response_error(response: reqwest::Response) -> String {
    let status = response.status();
    let text = response.text().await.unwrap_or_default();
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
        if let Some(detail) = value.get("detail").and_then(|v| v.as_str()) {
            return format!("{}: {}", status.as_u16(), detail);
        }
    }
    format!(
        "{}: {}",
        status.as_u16(),
        if text.is_empty() {
            "Erreur serveur"
        } else {
            &text
        }
    )
}

#[tauri::command]
async fn connector_state(
    state: State<'_, Arc<RuntimeState>>,
) -> Result<ConnectorStateView, String> {
    Ok(state.view.read().await.clone())
}

#[tauri::command]
async fn login(
    server_url: String,
    email: String,
    password: String,
    state: State<'_, Arc<RuntimeState>>,
) -> Result<ConnectorStateView, String> {
    let server_url = validate_server_url(&server_url)?;
    let email = email.trim().to_lowercase();
    if email.is_empty() || password.is_empty() {
        return Err("Identifiants incomplets".into());
    }

    let mut config = state.config.read().await.clone();
    if config.installation_id.is_empty() {
        config.installation_id = Uuid::new_v4().to_string();
    }
    if config.device_name.is_empty() {
        config.device_name = hostname::get()
            .ok()
            .and_then(|v| v.into_string().ok())
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "Poste professeur".into());
    }
    config.server_url = server_url;
    config.email = email;

    let response = state
        .client
        .post(api_url(&config.server_url, "/api/connectors/login"))
        .json(&LoginRequest {
            email: &config.email,
            password: &password,
            installation_id: &config.installation_id,
            device_name: &config.device_name,
            platform: platform_name(),
            arch: arch_name(),
            app_version: env!("CARGO_PKG_VERSION"),
        })
        .send()
        .await
        .map_err(|e| format!("Connexion impossible : {e}"))?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    let login: LoginResponse = response.json().await.map_err(|e| e.to_string())?;
    save_token(&config.installation_id, &login.token)?;
    atomic_json(&state.config_path, &config)?;
    *state.config.write().await = config.clone();
    *state.token.write().await = Some(login.token);
    let mut view = state.view.write().await;
    view.connected = true;
    view.email = config.email;
    view.server_url = config.server_url;
    view.device_name = config.device_name;
    view.status = "Connecté — détection des imprimantes…".into();
    Ok(view.clone())
}

#[tauri::command]
async fn logout(state: State<'_, Arc<RuntimeState>>) -> Result<ConnectorStateView, String> {
    let config = state.config.read().await.clone();
    let token = state.token.read().await.clone();
    if let Some(token) = token {
        let _ = state
            .client
            .post(api_url(&config.server_url, "/api/connectors/logout"))
            .bearer_auth(token)
            .send()
            .await;
    }
    delete_token(&config.installation_id);
    *state.token.write().await = None;
    let mut view = state.view.write().await;
    view.connected = false;
    view.status = "Déconnecté".into();
    view.printer_count = 0;
    view.current_job = None;
    view.jobs.clear();
    Ok(view.clone())
}

#[tauri::command]
async fn pause_worker(paused: bool, state: State<'_, Arc<RuntimeState>>) -> Result<(), String> {
    let _gate = state.job_gate.lock().await;
    if paused && state.view.read().await.current_job.is_some() {
        return Err("Attendez la fin de l'envoi à l'imprimante".into());
    }
    *state.paused.write().await = paused;
    Ok(())
}

#[cfg(target_os = "macos")]
fn enumerate_printers() -> Result<Vec<LocalPrinter>, String> {
    let destinations = Command::new("/usr/bin/lpstat")
        .arg("-e")
        .output()
        .map_err(|e| format!("lpstat indisponible : {e}"))?;
    if !destinations.status.success() {
        return Err(String::from_utf8_lossy(&destinations.stderr)
            .trim()
            .to_string());
    }
    let default_output = Command::new("/usr/bin/lpstat").arg("-d").output().ok();
    let default_text = default_output
        .as_ref()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default();
    let default = default_text.split(':').nth(1).map(str::trim).unwrap_or("");
    Ok(String::from_utf8_lossy(&destinations.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(|name| LocalPrinter {
            name: name.to_string(),
            is_default: name == default,
        })
        .collect())
}

#[cfg(target_os = "windows")]
fn enumerate_printers() -> Result<Vec<LocalPrinter>, String> {
    let script = concat!(
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();",
        "$p=@(Get-CimInstance Win32_Printer | ForEach-Object {",
        "[pscustomobject]@{name=$_.Name;is_default=[bool]$_.Default}});",
        "ConvertTo-Json -InputObject $p -Compress"
    );
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .output()
        .map_err(|e| format!("Énumération Windows impossible : {e}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("Liste d'imprimantes invalide : {e}"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn enumerate_printers() -> Result<Vec<LocalPrinter>, String> {
    Err("Système non pris en charge".into())
}

async fn heartbeat(
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
) -> Result<usize, String> {
    let printers = tokio::task::spawn_blocking(enumerate_printers)
        .await
        .map_err(|e| e.to_string())??;
    let count = printers.len();
    let response = runtime
        .client
        .post(api_url(&config.server_url, "/api/connectors/heartbeat"))
        .bearer_auth(token)
        .json(&HeartbeatRequest {
            app_version: env!("CARGO_PKG_VERSION"),
            printers,
        })
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    Ok(count)
}

async fn claim(
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
) -> Result<Option<RemoteJob>, String> {
    let response = runtime
        .client
        .post(api_url(&config.server_url, "/api/connectors/jobs/claim"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    Ok(response
        .json::<ClaimResponse>()
        .await
        .map_err(|e| e.to_string())?
        .job)
}

async fn recent_jobs(
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
) -> Result<Vec<JobView>, String> {
    let response = runtime
        .client
        .get(api_url(&config.server_url, "/api/connectors/jobs"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    response
        .json::<Vec<JobView>>()
        .await
        .map_err(|e| e.to_string())
}

fn load_journal(path: &Path) -> HashMap<String, String> {
    fs::read(path)
        .ok()
        .and_then(|v| serde_json::from_slice(&v).ok())
        .unwrap_or_default()
}

async fn report_result(
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
    job_id: &str,
    status: &str,
    spool: &str,
    error: &str,
) -> Result<(), String> {
    let response = runtime
        .client
        .post(api_url(
            &config.server_url,
            &format!("/api/connectors/jobs/{job_id}/result"),
        ))
        .bearer_auth(token)
        .json(&JobResultRequest {
            status,
            spool_job_id: spool,
            error,
        })
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    Ok(())
}

async fn download_job(
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
    job: &RemoteJob,
) -> Result<PathBuf, String> {
    if job.size == 0 || job.size > MAX_PDF_BYTES {
        return Err("Taille du PDF refusée".into());
    }
    let response = runtime
        .client
        .get(api_url(&config.server_url, &job.download_url))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(response_error(response).await);
    }
    let bytes = response.bytes().await.map_err(|e| e.to_string())?;
    if bytes.len() != job.size || bytes.len() > MAX_PDF_BYTES || !bytes.starts_with(b"%PDF-") {
        return Err("Document téléchargé invalide".into());
    }
    let digest = format!("{:x}", Sha256::digest(&bytes));
    if digest != job.sha256.to_lowercase() {
        return Err("Empreinte du PDF incorrecte".into());
    }
    fs::create_dir_all(&runtime.cache_dir).map_err(|e| e.to_string())?;
    let path = runtime.cache_dir.join(format!("{}.pdf", job.id));
    let temp = runtime.cache_dir.join(format!("{}.part", job.id));
    fs::write(&temp, bytes).map_err(|e| e.to_string())?;
    atomic_replace(&temp, &path)?;
    Ok(path)
}

fn mac_print_args(job: &RemoteJob, path: &Path) -> Vec<String> {
    vec![
        "-d".into(),
        job.printer.clone(),
        "-n".into(),
        job.options.copies.clamp(1, 50).to_string(),
        "-o".into(),
        "media=A4".into(),
        "-o".into(),
        "print-scaling=none".into(),
        "-o".into(),
        "Collate=True".into(),
        "-o".into(),
        "outputorder=normal".into(),
        "-o".into(),
        format!(
            "sides={}",
            if job.options.duplex {
                "two-sided-long-edge"
            } else {
                "one-sided"
            }
        ),
        path.to_string_lossy().to_string(),
    ]
}

#[cfg(any(target_os = "windows", test))]
fn windows_print_args(job: &RemoteJob, path: &Path) -> Vec<String> {
    let settings = format!(
        "noscale,paper=A4,{},ignore-pdf-print-settings,{},{}x",
        if job.options.collate {
            "collate"
        } else {
            "nocollate"
        },
        if job.options.duplex {
            "duplexlong"
        } else {
            "simplex"
        },
        job.options.copies.clamp(1, 50)
    );
    vec![
        "-print-to".into(),
        job.printer.clone(),
        "-print-settings".into(),
        settings,
        "-silent".into(),
        path.to_string_lossy().to_string(),
    ]
}

fn run_with_timeout(program: &Path, args: &[String]) -> Result<String, String> {
    let mut child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Impossible de lancer l'impression : {e}"))?;
    let status = match child
        .wait_timeout(Duration::from_secs(60))
        .map_err(|e| e.to_string())?
    {
        Some(status) => status,
        None => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Le moteur d'impression ne répond pas après 60 secondes".into());
        }
    };
    let mut stdout = String::new();
    let mut stderr = String::new();
    if let Some(mut pipe) = child.stdout.take() {
        let _ = pipe.read_to_string(&mut stdout);
    }
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_string(&mut stderr);
    }
    if !status.success() {
        return Err(format!(
            "Échec impression ({status}) : {}",
            if stderr.trim().is_empty() {
                stdout.trim()
            } else {
                stderr.trim()
            }
        ));
    }
    Ok(stdout.trim().to_string())
}

fn print_document(_app: &AppHandle, job: &RemoteJob, path: &Path) -> Result<String, String> {
    if job.options.media != "A4"
        || job.options.scale != "none"
        || !job.options.collate
        || !matches!(job.pass_side.as_str(), "all" | "recto" | "verso")
        || job.status != "claimed"
    {
        return Err("Options d'impression serveur invalides".into());
    }
    // L'ordre final est déjà matérialisé dans le PDF par le serveur. Cette
    // valeur reste dans le contrat pour rendre ce choix auditable côté poste.
    let _reverse_was_applied = job.options.reverse_applied_to_pdf;
    #[cfg(target_os = "macos")]
    {
        return run_with_timeout(Path::new("/usr/bin/lp"), &mac_print_args(job, path));
    }
    #[cfg(target_os = "windows")]
    {
        let engine = _app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("binaries")
            .join("SumatraPDF.exe");
        if !engine.exists() {
            return Err("Moteur PDF Windows absent de l'installation".into());
        }
        return run_with_timeout(&engine, &windows_print_args(job, path));
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (_app, job, path);
        Err("Système non pris en charge".into())
    }
}

async fn process_job(
    app: &AppHandle,
    runtime: &Arc<RuntimeState>,
    config: &StoredConfig,
    token: &str,
    job: RemoteJob,
) -> Result<(), String> {
    {
        let mut view = runtime.view.write().await;
        view.current_job = Some(format!("{} — {}", job.title, job.printer));
        view.status = "Envoi à la file d’impression…".into();
    }
    let mut journal = load_journal(&runtime.journal_path);
    if journal.get(&job.id).map(String::as_str) == Some("submitting") {
        report_result(
            runtime,
            config,
            token,
            &job.id,
            "uncertain",
            "",
            "Redémarrage pendant l'envoi ; vérifier la file système",
        )
        .await?;
        journal.insert(job.id.clone(), "uncertain".into());
        atomic_json(&runtime.journal_path, &journal)?;
        runtime.view.write().await.current_job = None;
        return Ok(());
    }
    if journal.get(&job.id).map(String::as_str) == Some("submitted") {
        report_result(
            runtime,
            config,
            token,
            &job.id,
            "submitted",
            "déjà transmis",
            "",
        )
        .await?;
        runtime.view.write().await.current_job = None;
        return Ok(());
    }

    let path = match download_job(runtime, config, token, &job).await {
        Ok(path) => path,
        Err(error) => {
            report_result(runtime, config, token, &job.id, "failed", "", &error).await?;
            runtime.view.write().await.current_job = None;
            return Err(error);
        }
    };
    journal.insert(job.id.clone(), "submitting".into());
    atomic_json(&runtime.journal_path, &journal)?;

    let app_clone = app.clone();
    let job_clone = job.clone();
    let path_clone = path.clone();
    let printed =
        tokio::task::spawn_blocking(move || print_document(&app_clone, &job_clone, &path_clone))
            .await
            .map_err(|e| e.to_string())?;
    match printed {
        Ok(spool) => {
            journal.insert(job.id.clone(), "submitted".into());
            atomic_json(&runtime.journal_path, &journal)?;
            report_result(runtime, config, token, &job.id, "submitted", &spool, "").await?;
        }
        Err(error) => {
            journal.insert(job.id.clone(), "failed".into());
            atomic_json(&runtime.journal_path, &journal)?;
            report_result(runtime, config, token, &job.id, "failed", "", &error).await?;
            runtime.view.write().await.current_job = None;
            let _ = fs::remove_file(path);
            return Err(error);
        }
    }
    let _ = fs::remove_file(path);
    let mut view = runtime.view.write().await;
    view.current_job = None;
    view.status = "Connecté — prêt".into();
    Ok(())
}

async fn worker_loop(app: AppHandle, runtime: Arc<RuntimeState>) {
    let mut last_heartbeat = Instant::now() - Duration::from_secs(60);
    let mut last_jobs = Instant::now() - Duration::from_secs(60);
    loop {
        if *runtime.paused.read().await {
            tokio::time::sleep(Duration::from_secs(1)).await;
            continue;
        }
        let config = runtime.config.read().await.clone();
        let token = runtime.token.read().await.clone();
        if let Some(token) = token.filter(|_| !config.server_url.is_empty()) {
            if last_heartbeat.elapsed() >= Duration::from_secs(30) {
                match heartbeat(&runtime, &config, &token).await {
                    Ok(count) => {
                        let mut view = runtime.view.write().await;
                        view.connected = true;
                        view.printer_count = count;
                        if view.current_job.is_none() {
                            view.status = "Connecté — prêt".into();
                        }
                    }
                    Err(error) => {
                        runtime.view.write().await.status = format!("Connexion : {error}")
                    }
                }
                last_heartbeat = Instant::now();
            }
            {
                let _gate = runtime.job_gate.lock().await;
                if !*runtime.paused.read().await {
                    match claim(&runtime, &config, &token).await {
                        Ok(Some(job)) => {
                            if let Err(error) =
                                process_job(&app, &runtime, &config, &token, job).await
                            {
                                runtime.view.write().await.status = format!("Impression : {error}");
                            }
                        }
                        Ok(None) => {}
                        Err(error) => {
                            runtime.view.write().await.status = format!("Connexion : {error}")
                        }
                    }
                }
            }
            if last_jobs.elapsed() >= Duration::from_secs(5) {
                if let Ok(jobs) = recent_jobs(&runtime, &config, &token).await {
                    runtime.view.write().await.jobs = jobs;
                }
                last_jobs = Instant::now();
            }
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(autostart_plugin())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let cache_dir = app.path().app_cache_dir()?.join("print-jobs");
            fs::create_dir_all(&config_dir)?;
            let config_path = config_dir.join("connector.json");
            let config = load_config(&config_path);
            let token = read_token(&config.installation_id);
            let view = ConnectorStateView {
                connected: token.is_some(),
                email: config.email.clone(),
                server_url: config.server_url.clone(),
                device_name: config.device_name.clone(),
                status: if token.is_some() {
                    "Connexion…".into()
                } else {
                    "Déconnecté".into()
                },
                ..Default::default()
            };
            let runtime = Arc::new(RuntimeState {
                view: RwLock::new(view),
                config: RwLock::new(config),
                token: RwLock::new(token),
                paused: RwLock::new(false),
                job_gate: Mutex::new(()),
                client: Client::builder()
                    .connect_timeout(Duration::from_secs(10))
                    .timeout(Duration::from_secs(90))
                    .build()?,
                config_path,
                journal_path: config_dir.join("print-journal.json"),
                cache_dir,
            });
            app.manage(runtime.clone());
            tauri::async_runtime::spawn(worker_loop(app.handle().clone(), runtime));

            let open = MenuItem::with_id(app, "open", "Ouvrir", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quitter", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            connector_state,
            login,
            logout,
            pause_worker
        ])
        .run(tauri::generate_context!())
        .expect("échec du lancement de MathPrint Connector");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(duplex: bool) -> RemoteJob {
        RemoteJob {
            id: "j".into(),
            title: "Sujet".into(),
            printer: "Canon USB".into(),
            pass_side: "all".into(),
            status: "claimed".into(),
            options: PrintOptions {
                copies: 1,
                duplex,
                media: "A4".into(),
                scale: "none".into(),
                collate: true,
                reverse_applied_to_pdf: true,
            },
            sha256: "x".into(),
            size: 1,
            download_url: "/file".into(),
        }
    }

    #[test]
    fn mac_arguments_force_the_paper_contract() {
        let args = mac_print_args(&job(true), Path::new("/tmp/a.pdf")).join(" ");
        assert!(args.contains("media=A4"));
        assert!(args.contains("print-scaling=none"));
        assert!(args.contains("Collate=True"));
        assert!(args.contains("outputorder=normal"));
        assert!(args.contains("sides=two-sided-long-edge"));
    }

    #[test]
    fn windows_arguments_force_the_paper_contract() {
        let args = windows_print_args(&job(false), Path::new("C:\\job.pdf")).join(" ");
        assert!(args.contains("noscale"));
        assert!(args.contains("paper=A4"));
        assert!(args.contains("collate"));
        assert!(args.contains("simplex"));
        assert!(args.contains("ignore-pdf-print-settings"));
    }

    #[test]
    fn public_server_requires_https() {
        assert!(validate_server_url("https://mathprint.example").is_ok());
        assert!(validate_server_url("http://localhost:8787").is_ok());
        assert!(validate_server_url("http://mathprint.example").is_err());
    }
}
