<?php
/**
 * Manual CSI/stability re-evaluation for the Planet project (Phase 7.5).
 * MVP: intentionally unauthenticated; nginx/local deployment trust only.
 */
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

if (!in_array($_SERVER['REQUEST_METHOD'] ?? 'GET', ['GET', 'POST'], true)) {
    http_response_code(405);
    header('Allow: GET, POST');
    echo json_encode(['error' => 'method_not_allowed']);
    exit;
}

$input = [];
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
    $raw = file_get_contents('php://input');
    if ($raw !== false && trim($raw) !== '') {
        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            http_response_code(400);
            echo json_encode(['error' => 'invalid_json']);
            exit;
        }
        $input = $decoded;
    }
}

$projectId = (int)($input['project_id'] ?? $_GET['project_id'] ?? 0);
$days = (int)($input['days'] ?? $_GET['days'] ?? 0);
if ($projectId !== 1) {
    http_response_code(400);
    echo json_encode(['error' => 'unsupported_project', 'supported_project_id' => 1]);
    exit;
}
if (!in_array($days, [7, 30], true)) {
    http_response_code(400);
    echo json_encode(['error' => 'invalid_days', 'allowed' => [7, 30]]);
    exit;
}

$dbHost = getenv('METRICS_DB_HOST') ?: '127.0.0.1';
$dbPort = getenv('METRICS_DB_PORT') ?: '5434';
$dbName = getenv('METRICS_DB_NAME') ?: 'bizdnai';
$dbUser = getenv('METRICS_DB_USER') ?: 'bizdnai';
$dbPass = getenv('METRICS_DB_PASSWORD') ?: 'bizdnai';
$repo = '/home/bizdnai/planet';
$stableColumn = $days === 7 ? 'stable_after_7d' : 'stable_after_30d';

try {
    $pdo = new PDO(
        "pgsql:host={$dbHost};port={$dbPort};dbname={$dbName}",
        $dbUser,
        $dbPass,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );

    $runsStmt = $pdo->prepare("
        SELECT tr.id, tr.completed_at
        FROM task_runs tr
        JOIN tasks t ON t.id = tr.task_id
        WHERE t.project_id = :project_id
          AND tr.status = 'success'
          AND tr.completed_at > now() - make_interval(days => :days)
        ORDER BY tr.completed_at
    ");
    $runsStmt->bindValue(':project_id', $projectId, PDO::PARAM_INT);
    $runsStmt->bindValue(':days', $days, PDO::PARAM_INT);
    $runsStmt->execute();
    $runs = $runsStmt->fetchAll();

    $filesStmt = $pdo->prepare("
        SELECT DISTINCT path
        FROM run_artifacts
        WHERE run_id = :run_id AND artifact_type = 'git_diff' AND path <> ''
        ORDER BY path
    ");
    $markReopened = $pdo->prepare("UPDATE run_evaluations SET reopened = TRUE, {$stableColumn} = FALSE WHERE run_id = :run_id");
    $markStable = $pdo->prepare("UPDATE run_evaluations SET {$stableColumn} = TRUE WHERE run_id = :run_id");

    $markedStable = 0;
    $markedReopened = 0;
    foreach ($runs as $run) {
        $endedAt = new DateTimeImmutable((string)$run['completed_at']);
        $endedIso = $endedAt->format(DateTimeInterface::ATOM);

        // Resolve the repository state that existed when the run ended.
        $shaCommand = 'git -C ' . escapeshellarg($repo)
            . ' log --until=' . escapeshellarg($endedIso)
            . ' -1 --format=%H 2>/dev/null';
        $baseSha = trim((string)shell_exec($shaCommand));

        $filesStmt->execute([':run_id' => (int)$run['id']]);
        $files = array_values(array_filter(array_column($filesStmt->fetchAll(), 'path')));
        $reopened = false;
        if ($baseSha !== '' && $files !== []) {
            $since = $endedAt->modify("+{$days} days")->format(DateTimeInterface::ATOM);
            $fileArgs = implode(' ', array_map('escapeshellarg', $files));
            $logCommand = 'git -C ' . escapeshellarg($repo)
                . ' log --since=' . escapeshellarg($since)
                . ' -1 --format=%H -- ' . $fileArgs . ' 2>/dev/null';
            $reopened = trim((string)shell_exec($logCommand)) !== '';
        }

        $statement = $reopened ? $markReopened : $markStable;
        $statement->execute([':run_id' => (int)$run['id']]);
        if ($statement->rowCount() > 0) {
            if ($reopened) {
                $markedReopened++;
            } else {
                $markedStable++;
            }
        }
    }

    echo json_encode([
        'revaled_runs' => count($runs),
        'marked_stable' => $markedStable,
        'marked_reopened' => $markedReopened,
    ], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    error_log('[metrics api_reval] ' . $e->getMessage());
    echo json_encode(['error' => 'revalidation_failed']);
}
