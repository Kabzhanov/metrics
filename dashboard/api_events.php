<?php
declare(strict_types=1);
/**
 * REST API endpoint для event-collector (Phase 4.5, задача #1176).
 *
 * POST /metrics/api_events.php
 * Content-Type: application/json
 *
 * Body:
 *   {
 *     "session_id": "abc-123",      // optional
 *     "tool_name": "Edit",          // optional
 *     "event_type": "file_modified",// REQUIRED, см. EVENT_TYPES ниже
 *     "file_path": "/path/to.py",   // optional
 *     "command": "curl ...",        // optional
 *     "success": true,              // optional, default true
 *     "duration_ms": 42,            // optional
 *     "metadata": {...}             // optional, JSON object
 *   }
 *
 * Response:
 *   200 {"status":"ok","event_id":N,"run_id":M}
 *   400 {"status":"error","message":"..."}  // validation
 *   500 {"status":"error","message":"..."}  // db / unknown
 *
 * MVP: без auth. Rate-limit — Phase 9.
 *
 * Реализует базовый secret redaction (Phase 8.5) для command и metadata:
 *   sk-[A-Za-z0-9]{20,}    → sk-***REDACTED***
 *   ghp_[A-Za-z0-9]{36}    → ghp_***REDACTED***
 *   AKIA[A-Z0-9]{16}       → AKIA***REDACTED***
 *   xoxb-[A-Za-z0-9-]+     → xoxb-***REDACTED***
 */

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

// CORS preflight — не идём в БД
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

// Только POST
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    header('Allow: POST');
    echo json_encode(['status' => 'error', 'message' => 'method_not_allowed']);
    exit;
}

// --- whitelist event_type ---
const EVENT_TYPES = [
    'file_read',
    'file_created',
    'file_modified',
    'file_deleted',
    'command_started',
    'command_completed',
    'mcp_called',
    'tool_called',
    'tool_completed',
    'tool_error',
    'session_stopped',
    'subagent_stopped',
    'notification',
    'manual', // generic fallback
];

// --- helpers ---
function send_error(int $code, string $message): void {
    http_response_code($code);
    echo json_encode(['status' => 'error', 'message' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

function send_ok(int $event_id, int $run_id): void {
    echo json_encode([
        'status'  => 'ok',
        'event_id' => $event_id,
        'run_id'   => $run_id,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

function truncate(?string $s, int $max): ?string {
    if ($s === null) return null;
    if (strlen($s) <= $max) return $s;
    return substr($s, 0, $max - 1) . '…';
}

const MAX_FILE_PATH = 1024;
const MAX_COMMAND   = 4096;
const MAX_TOOL_NAME = 256;
const MAX_METADATA  = 8192;

/**
 * Базовый regex-redactor для секретов. Применяется к строке и к JSON-значениям
 * внутри metadata (рекурсивно, по всем string-узлам).
 *
 * @return string|array|null
 */
function redact_secrets($value) {
    if (is_string($value)) {
        // OpenAI / sk-proj- / sk- ; GitHub PAT; AWS Access Key; Slack bot token
        $patterns = [
            '/sk-[A-Za-z0-9_\-]{20,}/'   => 'sk-***REDACTED***',
            '/ghp_[A-Za-z0-9]{36}/'      => 'ghp_***REDACTED***',
            '/AKIA[A-Z0-9]{16}/'         => 'AKIA***REDACTED***',
            '/xoxb-[A-Za-z0-9\-]+/'      => 'xoxb-***REDACTED***',
        ];
        foreach ($patterns as $pat => $repl) {
            $value = preg_replace($pat, $repl, $value);
        }
        return $value;
    }
    if (is_array($value)) {
        $out = [];
        foreach ($value as $k => $v) {
            $out[$k] = redact_secrets($v);
        }
        return $out;
    }
    return $value;
}

// --- read body ---
$raw = file_get_contents('php://input');
if ($raw === false || $raw === '') {
    send_error(400, 'empty_body');
}

$body = json_decode($raw, true);
if (!is_array($body)) {
    send_error(400, 'invalid_json: ' . json_last_error_msg());
}

// --- validate required ---
$event_type = isset($body['event_type']) && is_string($body['event_type'])
    ? trim($body['event_type'])
    : '';

if ($event_type === '') {
    send_error(400, 'event_type_required');
}
if (!in_array($event_type, EVENT_TYPES, true)) {
    send_error(400, 'event_type_not_in_whitelist: ' . $event_type);
}

// --- extract & sanitize ---
$session_id  = isset($body['session_id']) && is_string($body['session_id'])
    ? truncate($body['session_id'], 256)
    : null;
$tool_name   = isset($body['tool_name']) && is_string($body['tool_name'])
    ? truncate($body['tool_name'], MAX_TOOL_NAME)
    : null;
$file_path   = isset($body['file_path']) && is_string($body['file_path'])
    ? truncate($body['file_path'], MAX_FILE_PATH)
    : null;
$command_raw = isset($body['command']) && is_string($body['command'])
    ? $body['command']
    : null;
$duration_ms = null;
if (isset($body['duration_ms'])) {
    if (is_int($body['duration_ms'])) {
        $duration_ms = $body['duration_ms'];
    } elseif (is_numeric($body['duration_ms'])) {
        $duration_ms = (int)$body['duration_ms'];
    } else {
        send_error(400, 'duration_ms_must_be_integer');
    }
    if ($duration_ms < 0) {
        send_error(400, 'duration_ms_negative');
    }
}
$success = isset($body['success']) ? (bool)$body['success'] : true;

// metadata — JSON object (или null)
$metadata_in = $body['metadata'] ?? null;
if ($metadata_in !== null && !is_array($metadata_in)) {
    send_error(400, 'metadata_must_be_object');
}

// --- redact secrets ---
$command_redacted = $command_raw !== null
    ? truncate(redact_secrets($command_raw), MAX_COMMAND)
    : null;
$metadata_redacted = $metadata_in !== null
    ? redact_secrets($metadata_in)
    : [];

// вставим session_id в metadata для трассировки (если передан и ещё не там)
if ($session_id !== null && !isset($metadata_redacted['session_id'])) {
    $metadata_redacted['session_id'] = $session_id;
}
// метка источника — чтобы отличать от hook-collect
$metadata_redacted['_source_detail'] = 'rest-api';

$metadata_json = json_encode(
    $metadata_redacted,
    JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
);
if ($metadata_json === false) {
    send_error(400, 'metadata_not_serializable: ' . json_last_error_msg());
}
$metadata_json = truncate($metadata_json, MAX_METADATA);

// --- DB ---
$dbHost = getenv('METRICS_DB_HOST') ?: '127.0.0.1';
$dbPort = getenv('METRICS_DB_PORT') ?: '5434';
$dbName = getenv('METRICS_DB_NAME') ?: 'bizdnai';
$dbUser = getenv('METRICS_DB_USER') ?: 'bizdnai';
$dbPass = getenv('METRICS_DB_PASSWORD') ?: 'bizdnai';

try {
    $pdo = new PDO(
        "pgsql:host={$dbHost};port={$dbPort};dbname={$dbName}",
        $dbUser,
        $dbPass,
        [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );
} catch (Throwable $e) {
    error_log('[api_events] db_connect_failed: ' . $e->getMessage());
    send_error(500, 'db_unavailable');
}

// 1) SELECT текущий in_progress run
try {
    $stmt = $pdo->prepare(
        "SELECT id FROM task_runs "
        . "WHERE status='in_progress' "
        . "ORDER BY started_at DESC LIMIT 1"
    );
    $stmt->execute();
    $row = $stmt->fetch();
} catch (Throwable $e) {
    error_log('[api_events] select_run_failed: ' . $e->getMessage());
    send_error(500, 'select_run_failed');
}

if (!$row || !isset($row['id'])) {
    // Нет активной задачи — событие некуда привязать.
    // 503 вместо 500: состояние корректное, просто нечего мониторить.
    http_response_code(503);
    echo json_encode([
        'status'  => 'error',
        'message' => 'no_in_progress_run',
        'hint'    => 'no task_runs row with status=in_progress; nothing to attach events to',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}
$run_id = (int)$row['id'];

// 2) INSERT в run_events
try {
    $stmt = $pdo->prepare(
        "INSERT INTO run_events "
        . "(run_id, event_type, timestamp, source, tool_name, file_path, "
        . " command, duration_ms, success, metadata) VALUES "
        . " (:run_id, :event_type, now(), :source, :tool_name, :file_path, "
        . "  :command, :duration_ms, :success, CAST(:metadata AS jsonb)) "
        . "RETURNING id"
    );
    $stmt->bindValue(':run_id',      $run_id, PDO::PARAM_INT);
    $stmt->bindValue(':event_type',  $event_type, PDO::PARAM_STR);
    $stmt->bindValue(':source',      'rest-api', PDO::PARAM_STR);
    $stmt->bindValue(':tool_name',   $tool_name, $tool_name === null ? PDO::PARAM_NULL : PDO::PARAM_STR);
    $stmt->bindValue(':file_path',   $file_path, $file_path === null ? PDO::PARAM_NULL : PDO::PARAM_STR);
    $stmt->bindValue(':command',     $command_redacted, $command_redacted === null ? PDO::PARAM_NULL : PDO::PARAM_STR);
    if ($duration_ms === null) {
        $stmt->bindValue(':duration_ms', null, PDO::PARAM_NULL);
    } else {
        $stmt->bindValue(':duration_ms', $duration_ms, PDO::PARAM_INT);
    }
    $stmt->bindValue(':success',     $success, PDO::PARAM_BOOL);
    $stmt->bindValue(':metadata',    $metadata_json, PDO::PARAM_STR);
    $stmt->execute();
    $inserted = $stmt->fetch();
    $event_id = (int)$inserted['id'];
} catch (Throwable $e) {
    error_log('[api_events] insert_failed: ' . $e->getMessage());
    send_error(500, 'insert_failed');
}

send_ok($event_id, $run_id);
