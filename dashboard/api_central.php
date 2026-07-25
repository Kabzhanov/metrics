<?php
/**
 * BizDNAi Metrics — Central collector endpoint (Phase F2, задача #1199 → #1176).
 *
 * Spec: /home/bizdnai/docs/bizdnai-metrics-federation.md
 *
 * Routes:
 *   POST  /metrics/api_central.php            — ingest a federation batch
 *   DELETE /metrics/api_central.php?token=…   — revoke a token (soft delete)
 *
 * Auth:        Bearer token (sha256 stored in central_tokens)
 * Rate limit:  100 POSTs / hour / token
 * Anti-poison: ALLOWED_MODEL_PREFIXES whitelist (kept in sync with share.py)
 * Idempotency: UNIQUE (batch_id, opaque_id) in central_metrics
 */

declare(strict_types=1);

const ALLOWED_MODEL_PREFIXES = [
    'claude-',
    'gpt-',
    'chatgpt-',
    'MiniMax-',
    'gemini-',
    'qwen-',
    'deepseek-',
    'codestral-',
    'llama-',
    'mistral-',
    'coder-',
    'code-',
];

const ALLOWED_TASK_TYPES = [
    'feature', 'bugfix', 'refactor', 'docs', 'test',
    'review', 'ops', 'research', 'unknown',
];

const ALLOWED_STATUSES = ['success', 'failed', 'interrupted'];

const RATE_LIMIT_PER_HOUR = 100;
const MAX_EVENTS_PER_BATCH = 1000;

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, DELETE, OPTIONS');
header('Access-Control-Allow-Headers: Authorization, Content-Type');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));

try {
    $pdo = db_connect();
} catch (Throwable $e) {
    error_log('[metrics api_central] db_connect_failed: ' . $e->getMessage());
    respond(500, ['error' => 'db_unavailable']);
    exit;
}

if ($method === 'DELETE') {
    handle_revoke($pdo);
    exit;
}

if ($method !== 'POST') {
    header('Allow: POST, DELETE');
    respond(405, ['error' => 'method_not_allowed']);
    exit;
}

handle_ingest($pdo);

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

function handle_ingest(PDO $pdo): void
{
    $authHeader = (string)($_SERVER['HTTP_AUTHORIZATION'] ?? '');
    if (!preg_match('/^Bearer\s+(\S+)$/i', $authHeader, $matches)) {
        respond(401, ['error' => 'missing_bearer']);
        return;
    }
    $token = $matches[1];
    $tokenHash = hash('sha256', $token);

    $stmt = $pdo->prepare(
        'SELECT token_hash, created_at, revoked_at FROM central_tokens WHERE token_hash = :h'
    );
    $stmt->execute([':h' => $tokenHash]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row || $row['revoked_at'] !== null) {
        respond(403, ['error' => 'invalid_token']);
        return;
    }

    // Rate limit: 100 inserts / hour / token_hash.
    $rl = $pdo->prepare(
        "SELECT COUNT(*) FROM central_metrics
         WHERE submitter_hash = :h AND received_at > now() - interval '1 hour'"
    );
    $rl->execute([':h' => $tokenHash]);
    $count = (int)$rl->fetchColumn();
    if ($count >= RATE_LIMIT_PER_HOUR) {
        respond(429, ['error' => 'rate_limit', 'limit' => RATE_LIMIT_PER_HOUR]);
        return;
    }

    $raw = file_get_contents('php://input');
    if ($raw === false || $raw === '') {
        respond(400, ['error' => 'empty_body']);
        return;
    }
    $payload = json_decode($raw, true);
    if (!is_array($payload)) {
        respond(400, ['error' => 'invalid_json']);
        return;
    }

    $batchId = $payload['batch_id'] ?? null;
    if (!is_string($batchId) || !preg_match(
        '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i',
        $batchId
    )) {
        respond(400, ['error' => 'invalid_batch_id']);
        return;
    }

    $submittedAt = $payload['submitted_at'] ?? null;
    $submittedTs = parse_iso8601((string)$submittedAt);
    if ($submittedTs === null) {
        respond(400, ['error' => 'invalid_submitted_at']);
        return;
    }

    $events = $payload['events'] ?? null;
    if (!is_array($events) || count($events) === 0) {
        respond(400, ['error' => 'no_events']);
        return;
    }
    if (count($events) > MAX_EVENTS_PER_BATCH) {
        respond(413, ['error' => 'batch_too_large', 'limit' => MAX_EVENTS_PER_BATCH]);
        return;
    }

    $tokenPrefix = substr($token, 0, 8);

    $insert = $pdo->prepare(
        'INSERT INTO central_metrics (
            batch_id, opaque_id, submitter_hash, submitted_at,
            model, task_type, started_at_day, duration_sec,
            cost_usd, status, files_changed_count, source_token_prefix
         ) VALUES (
            :batch_id, :opaque_id, :submitter_hash, :submitted_at,
            :model, :task_type, :started_at_day, :duration_sec,
            :cost_usd, :status, :files_changed_count, :source_token_prefix
         ) ON CONFLICT (batch_id, opaque_id) DO NOTHING'
    );

    $accepted = 0;
    $rejected = 0;
    $rejectionReasons = [];

    $pdo->beginTransaction();
    try {
        foreach ($events as $idx => $event) {
            if (!is_array($event)) {
                $rejected++;
                continue;
            }
            $validation = validate_event($event);
            if ($validation !== null) {
                $rejected++;
                if (count($rejectionReasons) < 5) {
                    $rejectionReasons[] = "event[$idx]: $validation";
                }
                continue;
            }

            $insert->execute([
                ':batch_id'             => $batchId,
                ':opaque_id'            => $event['opaque_id'],
                ':submitter_hash'       => $tokenHash,
                ':submitted_at'         => $submittedTs,
                ':model'                => $event['model'],
                ':task_type'            => $event['task_type'],
                ':started_at_day'       => $event['started_at_day'],
                ':duration_sec'         => $event['duration_sec'],
                ':cost_usd'             => $event['cost_usd'],
                ':status'               => $event['status'],
                ':files_changed_count'  => $event['files_changed_count'],
                ':source_token_prefix'  => $tokenPrefix,
            ]);
            if ($insert->rowCount() > 0) {
                $accepted++;
            } else {
                // duplicate (batch_id, opaque_id) — counted as accepted idempotently
                $accepted++;
            }
        }

        $pdo->prepare('UPDATE central_tokens SET last_used_at = now() WHERE token_hash = :h')
            ->execute([':h' => $tokenHash]);
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        error_log('[metrics api_central] ingest_failed: ' . $e->getMessage());
        respond(500, ['error' => 'insert_failed']);
        return;
    }

    respond(200, [
        'status'   => 'ok',
        'accepted' => $accepted,
        'rejected' => $rejected,
        'rejection_reasons' => $rejectionReasons,
        'batch_id' => $batchId,
    ]);
}

function handle_revoke(PDO $pdo): void
{
    $token = isset($_GET['token']) ? (string)$_GET['token'] : '';
    if ($token === '') {
        respond(400, ['error' => 'missing_token']);
        return;
    }
    $tokenHash = hash('sha256', $token);
    $prefix = substr($token, 0, 8);

    $stmt = $pdo->prepare(
        'UPDATE central_tokens SET revoked_at = now()
         WHERE token_hash = :h AND revoked_at IS NULL'
    );
    $stmt->execute([':h' => $tokenHash]);

    if ($stmt->rowCount() === 0) {
        // Either unknown or already revoked — idempotent.
        respond(200, [
            'status'        => 'noop',
            'token_prefix'  => $prefix,
            'reason'        => 'not_found_or_already_revoked',
        ]);
        return;
    }

    respond(200, [
        'status'       => 'revoked',
        'token_prefix' => $prefix,
    ]);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function validate_event(array $event): ?string
{
    $opaqueId = $event['opaque_id'] ?? null;
    if (!is_string($opaqueId) || !preg_match('/^[0-9a-f]{16}$/', $opaqueId)) {
        return 'invalid_opaque_id';
    }

    $model = $event['model'] ?? null;
    if (!is_string($model)) {
        return 'invalid_model';
    }
    $allowed = false;
    foreach (ALLOWED_MODEL_PREFIXES as $prefix) {
        if (str_starts_with($model, $prefix)) {
            $allowed = true;
            break;
        }
    }
    if (!$allowed) {
        return 'model_not_allowed';
    }

    $taskType = $event['task_type'] ?? 'unknown';
    if (!is_string($taskType) || !in_array($taskType, ALLOWED_TASK_TYPES, true)) {
        return 'invalid_task_type';
    }

    $day = $event['started_at_day'] ?? null;
    if (!is_string($day) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $day)) {
        return 'invalid_started_at_day';
    }

    $duration = $event['duration_sec'] ?? null;
    if ($duration !== null && (!is_int($duration) || $duration < 0 || $duration > 86400)) {
        return 'invalid_duration_sec';
    }

    $cost = $event['cost_usd'] ?? null;
    if ($cost !== null && (!is_numeric($cost) || (float)$cost < 0 || (float)$cost > 10000)) {
        return 'invalid_cost_usd';
    }

    $status = $event['status'] ?? null;
    if (!is_string($status) || !in_array($status, ALLOWED_STATUSES, true)) {
        return 'invalid_status';
    }

    if (array_key_exists('files_changed_count', $event)) {
        $files = $event['files_changed_count'];
        if ($files !== null && (!is_int($files) || $files < 0 || $files > 100000)) {
            return 'invalid_files_changed_count';
        }
    }

    // Normalize: ensure files_changed_count is null when not provided
    $event['opaque_id']            = $opaqueId;
    $event['model']                = $model;
    $event['task_type']            = $taskType;
    $event['started_at_day']       = $day;
    $event['duration_sec']         = $duration;
    $event['cost_usd']             = $cost === null ? null : (float)$cost;
    $event['status']               = $status;
    $event['files_changed_count']  = $event['files_changed_count'] ?? null;

    return null;
}

function parse_iso8601(string $value): ?string
{
    $ts = strtotime($value);
    if ($ts === false) {
        return null;
    }
    return gmdate('Y-m-d H:i:s', $ts);
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

function respond(int $status, array $body): void
{
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE);
}