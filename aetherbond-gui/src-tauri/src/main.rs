#![cfg_attr(
  all(not(debug_assertions), target_os = "windows"),
  windows_subsystem = "windows"
)]

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
struct LinkState {
    name: String,
    ip: String,
    is_online: bool,
    checked_at: String,
}

#[derive(Serialize, Deserialize, Debug)]
struct OrchestratorStatus {
    status: String,
    active_links: Option<serde_json::Value>,
    timestamp: i64,
}

// Commands mapped directly to Go Orchestrator REST endpoints via reqwest!
#[tauri::command]
async fn get_orchestrator_status() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let res = client
        .get("http://127.0.0.1:9100/api/status")
        .timeout(std::time::Duration::from_millis(1500))
        .send()
        .await
        .map_err(|e| format!("Orchestrator unreachable: {}", e))?;

    let payload = res
        .json::<serde_json::Value>()
        .await
        .map_err(|e| format!("Failed to parse status payload: {}", e))?;

    Ok(payload)
}

#[tauri::command]
async fn run_nat_discovery() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let res = client
        .get("http://127.0.0.1:9100/api/nat")
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| format!("Orchestrator unreachable during NAT: {}", e))?;

    let payload = res
        .json::<serde_json::Value>()
        .await
        .map_err(|e| format!("Failed to parse NAT payload: {}", e))?;

    Ok(payload)
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            get_orchestrator_status,
            run_nat_discovery
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
