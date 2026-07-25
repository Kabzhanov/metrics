<?php
/**
 * BizDNAi Metrics — Verified Metrics API (Phase 2, задача #1176;
 * Phase 7.7 — per-project required_checks, задача #1207).
 *
 * Источник правды: SQL view (БД bizdnai :5434).
 *   - без project_id     → metrics_verified_v               (default behavior)
 *   - с project_id=N>0   → metrics_verified_per_project_v   (per spec §8.1)
 *
 * Возвращает агрегаты: verified_success_rate, first_pass_success,
 * rework_count, human_intervention_rate, median/p75/p90 duration,
 * cost_per_verified_success, retry_rate, tool_failure_rate.
 *
 * Параметры:
 *   period     = 7 | 30 | 90   (по умолчанию 30)
 *   model      = <model-name>  (опц., фильтр по конкретной модели)
 *   project_id = N>0           (опц., задача #1207; per-project required_checks)
 *   format     = json|csv      (опц., по умолчанию json)
 *
 * Если run_evaluations пусты (verification_pending=true) — возвращает
 * нулевые/Null метрики, но НЕ 500.
 */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=60');

$period    = isset($_GET['period'])    ? (string)$_GET['period']    : '30';
$model     = isset($_GET['model'])     ? trim((string)$_GET['model'])     : '';
$projectId = isset($_GET['project_id']) ? (int)$_GET['project_id']  : 0;

if (!in_array($period, ['7', '30', '90'], true)) {
    $period = '30';
}
$days = (int)$period;

// project_id > 0 → новый view (Phase 7.7). 0 → backward compat.
$usePerProject = $projectId > 0;

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

// 2) Основной запрос. Phase 7.7 (задача #1207): если project_id > 0,
//    читаем из metrics_verified_per_project_v — там verified_success
//    считается по project.required_checks, а не по всем 5 флагам.
//    Старый view оставлен для backward-compat (project_id не задан).
if ($usePerProject) {
    $sql = "
        SELECT
            model,
            day::date::text AS day,
            total_runs,
            verified_success,
            verified_success_strict
        FROM metrics_verified_per_project_v
        WHERE project_id = :project_id
          AND day >= (CURRENT_DATE - make_interval(days => :days))
    ";
    $params = [':project_id' => $projectId, ':days' => $days];
    if ($model !== '') {
        $sql .= " AND model = :model";
        $params[':model'] = $model;
    }
    $sql .= " ORDER BY day DESC, model ASC";

    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll();

    // Поля, которых нет в per_project view (rework / duration / cost / etc.),
    // остаются null/0 — для обратной совместимости с фронтом verified.html.
    foreach ($rows as &$row) {
        $row['first_pass_runs']          = null;
        $row['rework_count']             = null;
        $row['rework_time_ratio']        = null;
        $row['rework_lines_ratio']       = null;
        $row['human_interventions']      = null;
        $row['median_duration_sec']      = null;
        $row['p75_duration_sec']         = null;
        $row['p90_duration_sec']         = null;
        $row['total_cost_usd']           = 0.0;
        $row['cost_per_verified_success']= null;
        $row['retry_rate']               = null;
        $row['tool_failure_rate']        = null;
    }
    unset($row);
} else {
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
}

// 3) Нормализация строк + пересчёт агрегатов по (model) для фронта
$by_model = [];
$days_set = [];
foreach ($rows as $r) {
    $m = $r['model'];
    $d = $r['day'];
    $days_set[$d] = true;
    $total  = (int)$r['total_runs'];
    $verif  = (int)$r['verified_success'];
    // Phase 7.7 (задача #1207): в per-project view часть метрик недоступна —
    // используем null вместо нулей, чтобы фронт их не считал нулевыми.
    $hasExt = array_key_exists('first_pass_runs', $r) && $r['first_pass_runs'] !== null;
    $fps    = $hasExt ? (int)$r['first_pass_runs']        : null;
    $rework = $hasExt ? (int)$r['rework_count']           : null;
    $hi     = $hasExt ? (int)$r['human_interventions']    : null;
    $cost   = array_key_exists('total_cost_usd', $r) && $r['total_cost_usd'] !== null
              ? (float)$r['total_cost_usd'] : null;

    $by_model[$m]['days'][$d] = [
        'total_runs'                 => $total,
        'verified_success'           => $verif,
        'verified_success_rate'      => $total > 0 ? round($verif / $total, 4) : 0,
        'first_pass_runs'            => $fps,
        'first_pass_success_rate'    => ($fps !== null && $total > 0) ? round($fps / $total, 4) : null,
        'rework_count'               => $rework,
        'rework_time_ratio'          => $r['rework_time_ratio'] !== null ? (float)$r['rework_time_ratio'] : null,
        'rework_lines_ratio'         => $r['rework_lines_ratio'] !== null ? (float)$r['rework_lines_ratio'] : null,
        'human_interventions'        => $hi,
        'human_intervention_rate'    => ($hi !== null && $total > 0) ? round($hi / $total, 4) : null,
        'median_duration_sec'        => $r['median_duration_sec'] !== null ? (float)$r['median_duration_sec'] : null,
        'p75_duration_sec'           => $r['p75_duration_sec'] !== null ? (float)$r['p75_duration_sec'] : null,
        'p90_duration_sec'           => $r['p90_duration_sec'] !== null ? (float)$r['p90_duration_sec'] : null,
        'total_cost_usd'             => $cost,
        'cost_per_verified_success'  => $r['cost_per_verified_success'] !== null ? (float)$r['cost_per_verified_success'] : null,
        'retry_rate'                 => $r['retry_rate'] !== null ? (float)$r['retry_rate'] : null,
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
    if ($fps !== null)    { $by_model[$m]['agg']['first_pass_runs']    += $fps; }
    if ($rework !== null) { $by_model[$m]['agg']['rework_count']       += $rework; }
    if ($hi !== null)     { $by_model[$m]['agg']['human_interventions']+= $hi; }
    if ($cost !== null)   { $by_model[$m]['agg']['total_cost_usd']     += $cost; }
}

// Сборка период-агрегатов с derived rates
$models_out = [];
foreach ($by_model as $m => &$bucket) {
    $a = $bucket['agg'];
    $total = $a['total_runs'];
    $verif = $a['verified_success'];
    // Extended-метрики считаются "доступными", только если хотя бы в одном
    // дневном ряде были ненулевые значения — иначе per-project-ветка даёт 0.
    $hasExt = $usePerProject
        ? false
        : ($a['first_pass_runs'] || $a['rework_count'] || $a['human_interventions'] || $a['total_cost_usd']);
    $models_out[] = [
        'model'                       => $m,
        'total_runs'                  => $total,
        'verified_success'            => $verif,
        'verified_success_rate'       => $total > 0 ? round($verif / $total, 4) : 0,
        'first_pass_runs'             => $hasExt ? $a['first_pass_runs'] : null,
        'first_pass_success_rate'     => $hasExt && $total > 0 ? round($a['first_pass_runs'] / $total, 4) : null,
        'rework_count'                => $hasExt ? $a['rework_count']    : null,
        'rework_ratio'                => $hasExt && $total > 0 ? round($a['rework_count'] / $total, 4) : null,
        'human_interventions'         => $hasExt ? $a['human_interventions'] : null,
        'human_intervention_rate'     => $hasExt && $total > 0 ? round($a['human_interventions'] / $total, 4) : null,
        'total_cost_usd'              => $hasExt ? round($a['total_cost_usd'], 4) : null,
        'cost_per_verified_success'   => ($hasExt && $verif > 0) ? round($a['total_cost_usd'] / $verif, 4) : null,
        'days'                        => $bucket['days'],
    ];
}
unset($bucket);

usort($models_out, fn($a, $b) => strcmp($a['model'], $b['model']));
ksort($days_set);
$days_list = array_keys($days_set);

// Period-wide KPI
$kpi_total = 0; $kpi_verif = 0;
$kpi_fps = null; $kpi_rework = null; $kpi_hi = null; $kpi_cost = null;
if (!$usePerProject) {
    $kpi_fps = 0; $kpi_rework = 0; $kpi_hi = 0; $kpi_cost = 0.0;
}
foreach ($models_out as $m) {
    $kpi_total   += $m['total_runs'];
    $kpi_verif   += $m['verified_success'];
    if (!$usePerProject) {
        $kpi_fps     += $m['first_pass_runs'];
        $kpi_rework  += $m['rework_count'];
        $kpi_hi      += $m['human_interventions'];
        $kpi_cost    += $m['total_cost_usd'];
    }
}

echo json_encode([
    'period_days'           => $days,
    'model_filter'          => $model,
    'project_id'            => $projectId,                  // Phase 7.7 (задача #1207)
    'required_checks_view'  => $usePerProject ? 'per_project' : 'all_flags',
    'verification_pending'  => $verification_pending,
    'eval_rows_total'       => $evalCount,
    'days'                  => $days_list,
    'models'                => $models_out,
    'kpis' => [
        'total_runs'                  => $kpi_total,
        'verified_success'            => $kpi_verif,
        'verified_success_rate'       => $kpi_total > 0 ? round($kpi_verif / $kpi_total, 4) : 0,
        'first_pass_success_rate'     => ($kpi_fps !== null && $kpi_total > 0) ? round($kpi_fps / $kpi_total, 4) : null,
        'rework_ratio'                => ($kpi_rework !== null && $kpi_total > 0) ? round($kpi_rework / $kpi_total, 4) : null,
        'human_intervention_rate'     => ($kpi_hi !== null && $kpi_total > 0) ? round($kpi_hi / $kpi_total, 4) : null,
        'total_cost_usd'              => $kpi_cost !== null ? round($kpi_cost, 4) : null,
        'cost_per_verified_success'   => ($kpi_cost !== null && $kpi_verif > 0) ? round($kpi_cost / $kpi_verif, 4) : null,
    ],
    'generated_at' => date('c'),
], JSON_UNESCAPED_UNICODE);