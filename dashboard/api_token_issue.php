<?php
/**
 * BizDNAi Metrics — Token issuance endpoint (Phase F2, задача #1199 → #1176).
 *
 * MVP open: no auth. Generates a random 32-byte token, stores only its SHA-256
 * hash. The plaintext token is shown to the requester exactly once via the
 * community page (which calls this endpoint via fetch()).
 *
 * Anti-spam: 1 token / hour / IP (REMOTE_ADDR).
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if (strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? '')) !== 'POST') {
    header('Allow: POST');
    http_response_code(405);
    echo json_encode(['error' => 'method_not_allowed']);
    exit;
}

try {
    $pdo = db_connect();
} catch (Throwable $e) {
    error_log('[metrics token_issue] db_connect_failed: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode(['error' => 'db_unavailable']);
    exit;
}

$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '0.0.0.0');

$rl = $pdo->prepare(
    "SELECT COUNT(*) FROM central_tokens
     WHERE issued_to_ip = :ip AND created_at > now() - interval '1 hour'"
);
$rl->execute([':ip' => $ip]);
if ((int)$rl->fetchColumn() > 0) {
    http_response_code(429);
    echo json_encode([
        'error'       => 'rate_limit',
        'retry_after' => 3600,
        'message'     => 'One token per IP per hour. Please try again later.',
    ]);
    exit;
}

$raw = file_get_contents('php://input');
$note = '';
if ($raw !== false && $raw !== '') {
    $decoded = json_decode($raw, true);
    if (is_array($decoded) && isset($decoded['note']) && is_string($decoded['note'])) {
        $note = substr(trim($decoded['note']), 0, 100);
    }
}

$token = generate_token();
$tokenHash = hash('sha256', $token);
$prefix = substr($token, 0, 8);

$stmt = $pdo->prepare(
    'INSERT INTO central_tokens (token_hash, note, issued_to_ip)
     VALUES (:h, :note, :ip)'
);
$stmt->execute([':h' => $tokenHash, ':note' => $note, ':ip' => $ip]);

http_response_code(201);
echo json_encode([
    'token'        => $token,
    'token_hash'   => $tokenHash,
    'token_prefix' => $prefix,
    'issued_at'    => gmdate('c'),
    'note'         => $note,
    'install'      => [
        'config_path' => '~/.config/mcp-metrics/config.yaml',
        'snippet'     => "share:\n  enabled: true\n  endpoint: https://bizdnai.com/metrics/api_central.php\n  token: \"$token\"",
    ],
], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generate_token(): string
{
    // 32 random bytes → 43-char urlsafe base64 (no padding).
    return rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
}

function db_connect(): PDO
{
    $host = getenv('METRICS_DB_HOST') ?: '127.0.0.1';
    $port = getenv('METRICS_DB_PORT') ?: '5434';
    $name = getenv('METRICS_DB_NAME') ?: 'bizdnai';
    $user = getenv('METRICS_DB_USER') ?: 'bizdnai';
    $pass = getenv('METRICS_DB_PASSWORD') ?: 'bizdnai';
    return new PDO(
        "pgsql:host={$host};port={$port};dbname={$name}",
        $user,
        $pass,
        [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );
}