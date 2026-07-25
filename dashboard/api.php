<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=60');

$period = isset($_GET['period']) ? (string)$_GET['period'] : '30';
$model  = isset($_GET['model'])  ? trim((string)$_GET['model']) : '';

// period: "7", "30", "90" (days)
if (!in_array($period, ['7', '30', '90'], true)) {
    $period = '30';
}
$days = (int)$period;

// Postgres connection (creds from env, fallback to local-only dev defaults)
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
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );
} catch (Throwable $e) {
    http_response_code(500);
    // Не светим детали в проде; логируем на сервере
    error_log('[metrics api] db_connect_failed: ' . $e->getMessage());
    echo json_encode(['error' => 'db_unavailable']);
    exit;
}

// 1) daily aggregates by model (success_rate / avg_duration / avg_cost)
$sql = "
    SELECT
        model,
        day::date::text AS day,
        total,
        ok,
        failed,
        human_stop,
        avg_duration_sec,
        avg_cost_usd,
        total_cost_usd
    FROM metrics_tasks_v
    WHERE day >= now() - make_interval(days => :days)
";
$params = [':days' => $days];
if ($model !== '') {
    $sql .= " AND model = :model";
    $params[':model'] = $model;
}
$sql .= " ORDER BY day ASC, model ASC";

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// Reshape: group by day, series per model
$days_set = [];
$models_set = [];
$by_model_day = [];
foreach ($rows as $r) {
    $m = $r['model'];
    $d = $r['day'];
    $days_set[$d] = true;
    $models_set[$m] = true;
    $by_model_day[$m][$d] = [
        'rate'      => ($r['total'] > 0) ? round($r['ok'] / $r['total'], 4) : 0,
        'avg_duration' => $r['avg_duration_sec'] !== null ? (float)$r['avg_duration_sec'] : 0,
        'avg_cost'     => $r['avg_cost_usd'] !== null ? (float)$r['avg_cost_usd'] : 0,
        'total'        => (int)$r['total'],
        'ok'           => (int)$r['ok'],
        'failed'       => (int)$r['failed'],
    ];
}
ksort($days_set);
$days_list = array_keys($days_set);

function build_series(array $by_model_day, array $days_list, string $key): array {
    $out = [];
    foreach ($by_model_day as $model => $days) {
        $values = [];
        foreach ($days_list as $d) {
            $values[] = isset($days[$d]) ? $days[$d][$key] : null;
        }
        $out[] = ['model' => $model, 'data' => $values];
    }
    return $out;
}

$success_rate_series  = build_series($by_model_day, $days_list, 'rate');
$avg_duration_series  = build_series($by_model_day, $days_list, 'avg_duration');
$avg_cost_series      = build_series($by_model_day, $days_list, 'avg_cost');

// 2) tasks-by-model totals (across full period)
$totals_by_model = [];
foreach ($by_model_day as $m => $by_day) {
    $t = 0; $ok = 0; $fail = 0; $cost = 0.0;
    foreach ($by_day as $d => $v) {
        $t += $v['total'];
    }
    // re-query for total cost
    $totals_by_model[] = ['model' => $m, 'total' => $t];
}

// 3) latest 20 tasks (filtered by model only — period is for charts)
$sql20 = "SELECT task_id, model, status, duration_sec, cost_usd, started_at FROM metrics_tasks";
$params20 = [];
if ($model !== '') {
    $sql20 .= " WHERE model = :model";
    $params20[':model'] = $model;
}
$sql20 .= " ORDER BY started_at DESC NULLS LAST LIMIT 20";
$stmt20 = $pdo->prepare($sql20);
$stmt20->execute($params20);
$recent = $stmt20->fetchAll();

$recent_out = array_map(function ($r) {
    return [
        'task_id'      => $r['task_id'],
        'model'        => $r['model'],
        'status'       => $r['status'],
        'duration_sec' => $r['duration_sec'] !== null ? (int)$r['duration_sec'] : null,
        'cost_usd'     => $r['cost_usd'] !== null ? (float)$r['cost_usd'] : null,
        'started_at'   => $r['started_at'],
    ];
}, $recent);

// 4) overall KPIs across period
$kpi_total = 0; $kpi_ok = 0; $kpi_cost = 0.0;
foreach ($by_model_day as $by_day) {
    foreach ($by_day as $v) {
        $kpi_total += $v['total'];
        $kpi_ok    += $v['ok'];
    }
}
$kpi_rate = $kpi_total > 0 ? round($kpi_ok / $kpi_total, 4) : 0;

// avg duration across period (weighted)
$sum_dur = 0.0; $sum_cnt = 0;
foreach ($by_model_day as $by_day) {
    foreach ($by_day as $v) {
        if ($v['avg_duration'] > 0 && $v['total'] > 0) {
            $sum_dur += $v['avg_duration'] * $v['total'];
            $sum_cnt += $v['total'];
        }
    }
}
$kpi_avg_dur = $sum_cnt > 0 ? round($sum_dur / $sum_cnt, 1) : 0;

// total cost from view
$sql_cost = "SELECT COALESCE(SUM(total_cost_usd),0) AS c FROM metrics_tasks_v WHERE day >= now() - make_interval(days => :days)";
$params_cost = [':days' => $days];
if ($model !== '') {
    $sql_cost .= " AND model = :model";
    $params_cost[':model'] = $model;
}
$stmt_c = $pdo->prepare($sql_cost);
$stmt_c->execute($params_cost);
$kpi_cost = (float)$stmt_c->fetchColumn();

// list of models for dropdown
$models_list = array_keys($models_set);
sort($models_list);

echo json_encode([
    'period_days' => $days,
    'model_filter' => $model,
    'days'         => $days_list,
    'models'       => $models_list,
    'success_rate' => $success_rate_series,
    'avg_duration' => $avg_duration_series,
    'avg_cost'     => $avg_cost_series,
    'totals_by_model' => $totals_by_model,
    'kpis' => [
        'total'           => $kpi_total,
        'success_rate'    => $kpi_rate,
        'avg_duration_sec'=> $kpi_avg_dur,
        'total_cost_usd'  => round($kpi_cost, 2),
    ],
    'recent' => $recent_out,
    'generated_at' => date('c'),
], JSON_UNESCAPED_UNICODE);
