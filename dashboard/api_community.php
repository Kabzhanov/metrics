<?php

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: public, max-age=300, stale-while-revalidate=60');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET');
header('X-Content-Type-Options: nosniff');

function respondJson(array $payload, int $status = 200): never
{
    http_response_code($status);
    echo json_encode(
        $payload,
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION | JSON_THROW_ON_ERROR
    );
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    respondJson(['error' => 'method_not_allowed'], 405);
}

function connectDatabase(): PDO
{
    $host = getenv('METRICS_DB_HOST') ?: '127.0.0.1';
    $port = getenv('METRICS_DB_PORT') ?: '5434';
    $name = getenv('METRICS_DB_NAME') ?: 'bizdnai';
    $user = getenv('METRICS_DB_USER') ?: 'bizdnai';
    $password = getenv('METRICS_DB_PASSWORD') ?: 'bizdnai';

    return new PDO(
        sprintf('pgsql:host=%s;port=%s;dbname=%s', $host, $port, $name),
        $user,
        $password,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]
    );
}

function nullableFloat(mixed $value): ?float
{
    return $value === null ? null : (float) $value;
}

try {
    $pdo = connectDatabase();
    $pdo->exec("SET TIME ZONE 'UTC'");

    $summaryRow = $pdo->query(<<<'SQL'
        SELECT
            COUNT(*) AS total_events,
            COUNT(DISTINCT submitter_hash) AS unique_submitters,
            COUNT(DISTINCT model) AS unique_models,
            COUNT(DISTINCT task_type) AS unique_task_types,
            MIN(received_at)::date AS period_start,
            MAX(received_at)::date AS period_end
        FROM central_metrics
        WHERE received_at > NOW() - INTERVAL '90 days'
        SQL)->fetch() ?: [];

    $modelRows = $pdo->query(<<<'SQL'
        SELECT
            model,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN status = 'success' THEN 1.0 ELSE 0.0 END), 4) AS success_rate,
            ROUND(AVG(duration_sec)::numeric, 1) AS avg_duration_sec,
            ROUND(AVG(cost_usd)::numeric, 4) AS avg_cost_usd
        FROM central_metrics
        GROUP BY model
        ORDER BY n DESC, model ASC
        SQL)->fetchAll();

    // The privacy-safe federation schema does not contain the seven inputs for
    // the full MQI. Until it does, MQI Preview is the observed success rate;
    // sample size is returned so the UI never presents it without context.
    $taskRows = $pdo->query(<<<'SQL'
        WITH task_model_stats AS (
            SELECT
                task_type,
                model,
                COUNT(*) AS n,
                ROUND(AVG(CASE WHEN status = 'success' THEN 1.0 ELSE 0.0 END), 4) AS mqi
            FROM central_metrics
            GROUP BY task_type, model
        ), ranked AS (
            SELECT
                task_type,
                model,
                n,
                mqi,
                ROW_NUMBER() OVER (
                    PARTITION BY task_type
                    ORDER BY mqi DESC, n DESC, model ASC
                ) AS rnk
            FROM task_model_stats
        )
        SELECT task_type, model AS best_model, n, mqi AS best_mqi
        FROM ranked
        WHERE rnk = 1
        ORDER BY task_type ASC
        SQL)->fetchAll();

    $dayRows = $pdo->query(<<<'SQL'
        SELECT
            date_trunc('day', received_at)::date AS day,
            COUNT(*) AS events
        FROM central_metrics
        GROUP BY 1
        ORDER BY 1 ASC
        SQL)->fetchAll();

    $response = [
        'summary' => [
            'total_events' => (int) ($summaryRow['total_events'] ?? 0),
            'unique_submitters' => (int) ($summaryRow['unique_submitters'] ?? 0),
            'unique_models' => (int) ($summaryRow['unique_models'] ?? 0),
            'unique_task_types' => (int) ($summaryRow['unique_task_types'] ?? 0),
            'period_start' => $summaryRow['period_start'] ?? null,
            'period_end' => $summaryRow['period_end'] ?? null,
        ],
        'by_model' => array_map(
            static fn(array $row): array => [
                'model' => (string) $row['model'],
                'n' => (int) $row['n'],
                'success_rate' => nullableFloat($row['success_rate']),
                'avg_duration_sec' => nullableFloat($row['avg_duration_sec']),
                'avg_cost_usd' => nullableFloat($row['avg_cost_usd']),
            ],
            $modelRows
        ),
        'by_task_type' => array_map(
            static fn(array $row): array => [
                'task_type' => (string) $row['task_type'],
                'n' => (int) $row['n'],
                'best_model' => (string) $row['best_model'],
                'best_mqi' => nullableFloat($row['best_mqi']),
            ],
            $taskRows
        ),
        'by_day' => array_map(
            static fn(array $row): array => [
                'day' => (string) $row['day'],
                'events' => (int) $row['events'],
            ],
            $dayRows
        ),
    ];

    respondJson($response);
} catch (Throwable $error) {
    error_log('metrics/api_community.php: ' . $error->getMessage());
    respondJson(['error' => 'internal_error'], 500);
}
