<?php
/**
 * BizDNAi Metrics — Verified Metrics API (Phase 2, задача #1176).
 *
 * Источник правды: SQL view `metrics_verified_v` (БД bizdnai :5434).
 * Возвращает агрегаты: verified_success_rate, first_pass_success,
 * rework_count, human_intervention_rate, median/p75/p90 duration,
 * cost_per_verified_success, retry_rate, tool_failure_rate.
 *
 * Параметры:
 *   period = 7 | 30 | 90   (по умолчанию 30)
 *   model  = <model-name>  (опц., фильтр по конкретной модели)
 *   format = json|csv      (опц., по умолчанию json)
 *
 * Если run_evaluations пусты (verification_pending=true) — возвращает
 * нулевые/Null метрики, но НЕ 500.
 */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=60');

$period = isset($_GET['period']) ? (string)$_GET['period'] : '30';
$model  = isset($_GET['model'])  ? trim((string)$_GET['model'])  : '';

if (!in_array($period, ['7', '30', '90'], true)) {
    $period = '30';
}
$days = (int)$period;

// Postgres connection
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
    http_response_code(500);
    error_log('[metrics api_verified] db_connect_failed: ' . $e->getMessage());
    echo json_encode(['error' => 'db_unavailable']);
    exit;
}

// 1) Проверка наличия evaluations (verification_pending)
$stmtV = $pdo->query("SELECT COUNT(*) FROM run_evaluations");
$evalCount = (int)$stmtV->fetchColumn();
$verification_pending = $evalCount === 0;

// 2) Основной запрос из view, фильтрация по дню прямо в SQL
$sql = "
    SELECT
        model,
        day::date::text AS day,
        total_runs,
        verified_success,
        first_pass_runs,
        rework_count,
        rework_time_ratio,
        rework_lines_ratio,
        human_interventions,
        median_duration_sec,
        p75_duration_sec,
        p90_duration_sec,
        total_cost_usd,
        cost_per_verified_success,
        retry_rate,
        tool_failure_rate
    FROM metrics_verified_v
    WHERE day >= (CURRENT_DATE - make_interval(days => :days))
";
$params = [':days' => $days];
if ($model !== '') {
    $sql .= " AND model = :model";
    $params[':model'] = $model;
}
$sql .= " ORDER BY day DESC, model ASC";

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// 3) Нормализация строк + пересчёт агрегатов по (model) для фронта
$by_model = [];
$days_set = [];
foreach ($rows as $r) {
    $m = $r['model'];
    $d = $r['day'];
    $days_set[$d] = true;
    $total  = (int)$r['total_runs'];
    $verif  = (int)$r['verified_success'];
    $fps    = (int)$r['first_pass_runs'];
    $rework = (int)$r['rework_count'];
    $hi     = (int)$r['human_interventions'];

    $by_model[$m]['days'][$d] = [
        'total_runs'                 => $total,
        'verified_success'           => $verif,
        'verified_success_rate'      => $total > 0 ? round($verif / $total, 4) : 0,
        'first_pass_runs'            => $fps,
        'first_pass_success_rate'    => $total > 0 ? round($fps / $total, 4) : 0,
        'rework_count'               => $rework,
        'rework_time_ratio'          => $r['rework_time_ratio'] !== null ? (float)$r['rework_time_ratio'] : null,
        'rework_lines_ratio'         => $r['rework_lines_ratio'] !== null ? (float)$r['rework_lines_ratio'] : null,
        'human_interventions'        => $hi,
        'human_intervention_rate'    => $total > 0 ? round($hi / $total, 4) : 0,
        'median_duration_sec'        => $r['median_duration_sec'] !== null ? (float)$r['median_duration_sec'] : null,
        'p75_duration_sec'           => $r['p75_duration_sec'] !== null ? (float)$r['p75_duration_sec'] : null,
        'p90_duration_sec'           => $r['p90_duration_sec'] !== null ? (float)$r['p90_duration_sec'] : null,
        'total_cost_usd'             => (float)$r['total_cost_usd'],
        'cost_per_verified_success'  => $r['cost_per_verified_success'] !== null ? (float)$r['cost_per_verified_success'] : null,
        'retry_rate'                 => $r['retry_rate'] !== null ? (float)$r['retry_rate'] : 0,
        'tool_failure_rate'          => $r['tool_failure_rate'] !== null ? (float)$r['tool_failure_rate'] : null,
    ];

    // Сумматоры по модели за период
    if (!isset($by_model[$m]['agg'])) {
        $by_model[$m]['agg'] = [
            'total_runs' => 0, 'verified_success' => 0,
            'first_pass_runs' => 0, 'rework_count' => 0,
            'human_interventions' => 0, 'total_cost_usd' => 0.0,
        ];
    }
    $by_model[$m]['agg']['total_runs']         += $total;
    $by_model[$m]['agg']['verified_success']   += $verif;
    $by_model[$m]['agg']['first_pass_runs']    += $fps;
    $by_model[$m]['agg']['rework_count']       += $rework;
    $by_model[$m]['agg']['human_interventions']+= $hi;
    $by_model[$m]['agg']['total_cost_usd']     += (float)$r['total_cost_usd'];
}

// Сборка период-агрегатов с derived rates
$models_out = [];
foreach ($by_model as $m => &$bucket) {
    $a = $bucket['agg'];
    $total = $a['total_runs'];
    $verif = $a['verified_success'];
    $models_out[] = [
        'model'                       => $m,
        'total_runs'                  => $total,
        'verified_success'            => $verif,
        'verified_success_rate'       => $total > 0 ? round($verif / $total, 4) : 0,
        'first_pass_runs'             => $a['first_pass_runs'],
        'first_pass_success_rate'     => $total > 0 ? round($a['first_pass_runs'] / $total, 4) : 0,
        'rework_count'                => $a['rework_count'],
        'rework_ratio'                => $total > 0 ? round($a['rework_count'] / $total, 4) : 0,
        'human_interventions'         => $a['human_interventions'],
        'human_intervention_rate'     => $total > 0 ? round($a['human_interventions'] / $total, 4) : 0,
        'total_cost_usd'              => round($a['total_cost_usd'], 4),
        'cost_per_verified_success'   => $verif > 0 ? round($a['total_cost_usd'] / $verif, 4) : null,
        'days'                        => $bucket['days'],
    ];
}
unset($bucket);

usort($models_out, fn($a, $b) => strcmp($a['model'], $b['model']));
ksort($days_set);
$days_list = array_keys($days_set);

// Period-wide KPI
$kpi_total = 0; $kpi_verif = 0; $kpi_fps = 0; $kpi_rework = 0; $kpi_hi = 0; $kpi_cost = 0.0;
foreach ($models_out as $m) {
    $kpi_total   += $m['total_runs'];
    $kpi_verif   += $m['verified_success'];
    $kpi_fps     += $m['first_pass_runs'];
    $kpi_rework  += $m['rework_count'];
    $kpi_hi      += $m['human_interventions'];
    $kpi_cost    += $m['total_cost_usd'];
}

echo json_encode([
    'period_days'           => $days,
    'model_filter'          => $model,
    'verification_pending'  => $verification_pending,
    'eval_rows_total'       => $evalCount,
    'days'                  => $days_list,
    'models'                => $models_out,
    'kpis' => [
        'total_runs'                  => $kpi_total,
        'verified_success'            => $kpi_verif,
        'verified_success_rate'       => $kpi_total > 0 ? round($kpi_verif / $kpi_total, 4) : 0,
        'first_pass_success_rate'     => $kpi_total > 0 ? round($kpi_fps / $kpi_total, 4) : 0,
        'rework_ratio'                => $kpi_total > 0 ? round($kpi_rework / $kpi_total, 4) : 0,
        'human_intervention_rate'     => $kpi_total > 0 ? round($kpi_hi / $kpi_total, 4) : 0,
        'total_cost_usd'              => round($kpi_cost, 4),
        'cost_per_verified_success'   => $kpi_verif > 0 ? round($kpi_cost / $kpi_verif, 4) : null,
    ],
    'generated_at' => date('c'),
], JSON_UNESCAPED_UNICODE);