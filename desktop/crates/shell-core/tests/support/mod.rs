//! A tiny loopback HTTP server that answers like the local core from the shared fixtures.
//! Test-only; binds 127.0.0.1 on a random port.

#![allow(dead_code, clippy::unwrap_used, clippy::expect_used)]

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

pub fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../core/contracts/local-api")
}

pub fn fixture(name: &str) -> String {
    std::fs::read_to_string(fixtures_dir().join(name))
        .unwrap_or_else(|e| panic!("fixture {name}: {e}"))
}

#[derive(Clone, Debug)]
pub struct Recorded {
    pub method: String,
    pub path: String,
    pub headers: BTreeMap<String, String>,
    pub body: String,
}

pub struct FakeCore {
    pub port: u16,
    pub requests: Arc<Mutex<Vec<Recorded>>>,
    handle: Option<JoinHandle<()>>,
}

impl FakeCore {
    /// Serve `connections` requests, answering with fixtures. `test_fixture` is the body
    /// for `POST /v1/test-connection`. Requests without `Bearer <token>` get the
    /// unauthorized fixture with status 401.
    pub fn start(token: &str, test_fixture: &'static str, connections: usize) -> FakeCore {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let seen = requests.clone();
        let expected = format!("Bearer {token}");
        let handle = std::thread::spawn(move || {
            for _ in 0..connections {
                let Ok((mut stream, _)) = listener.accept() else {
                    return;
                };
                let rec = read_request(&mut stream);
                let authed = rec.headers.get("authorization") == Some(&expected);
                let (status, body) = if !authed {
                    (401, fixture("error-unauthorized.json"))
                } else {
                    match (rec.method.as_str(), rec.path.as_str()) {
                        ("GET", "/v1/health") => (200, fixture("health.json")),
                        ("GET", "/v1/settings") | ("PUT", "/v1/settings") => {
                            (200, fixture("settings.json"))
                        }
                        ("POST", "/v1/secrets/load") => {
                            (200, fixture("secrets-load-response.json"))
                        }
                        ("POST", "/v1/test-connection") => (200, fixture(test_fixture)),
                        _ => (404, fixture("error-unauthorized.json")),
                    }
                };
                seen.lock().unwrap().push(rec);
                let reason = if status == 200 { "OK" } else { "Error" };
                let reply = format!(
                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(reply.as_bytes());
            }
        });
        FakeCore {
            port,
            requests,
            handle: Some(handle),
        }
    }

    pub fn finish(mut self) -> Vec<Recorded> {
        if let Some(h) = self.handle.take() {
            h.join().unwrap();
        }
        self.requests.lock().unwrap().clone()
    }
}

fn read_request(stream: &mut TcpStream) -> Recorded {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 1024];
    let header_end = loop {
        let n = stream.read(&mut chunk).unwrap();
        assert!(n > 0, "client closed before sending headers");
        buf.extend_from_slice(&chunk[..n]);
        if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break pos + 4;
        }
    };
    let head = String::from_utf8_lossy(&buf[..header_end]).into_owned();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap();
    let mut parts = request_line.split(' ');
    let method = parts.next().unwrap().to_owned();
    let path = parts.next().unwrap().to_owned();
    let mut headers = BTreeMap::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_owned());
        }
    }
    let length: usize = headers
        .get("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    while buf.len() < header_end + length {
        let n = stream.read(&mut chunk).unwrap();
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    let body = String::from_utf8_lossy(&buf[header_end..]).into_owned();
    Recorded {
        method,
        path,
        headers,
        body,
    }
}
