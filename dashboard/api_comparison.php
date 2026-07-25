<?php
/**
 * BizDNAi Metrics — Comparison by Task Type API (Phase 6.5, задача #1176).
 *
 * Источник правды: SQL view `metrics_by_task_type_v` (БД bizdnai :5434).
 * Возвращает строки для comparison table: model × task_type с success_rate,
 * avg_duration_sec, avg_cost_usd, stability_rate, mqi_preview.
 *
 * Параметры:
 *   period     = 7 | 30 | 90                (по умолчанию 30)
 *   task_types = <name1>,<name2>,...        (опц., multi-select фильтр; "all"/пусто = без фильтра)
 *   task_type  = <name>                     (legacy, синоним task_types; single value)
 *   model      = <name>                     (опц., фильтр по модели)
 *
 * Возвращает:
 *   {
 *     period_days, model_filter, task_types_filter, task_types_count,
 *     rows: [...],
 *     task_types: [...] (список всех доступных task_type в БД — для chips),
 *     task_type_counts: {feature: 35, ...} (агрегаты по всем доступным),
 *     models: [...],
 *     kpis: {...},
 *     generated_at
 *   }
 * Если фильтр отрезал всё — rows = [], без 404.
 */

declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=60');

$period = isset($_GET['period']) ? (string)$_GET['period'] : '30';
$model  = isset($_GET['model'])  ? trim((string)$_GET['model'])  : '';

// task_types — multi-select (comma-separated). task_type — legacy single.
// "all" или пусто → без фильтра.
$rawTaskTypes = isset($_GET['task_types']) ? trim((string)$_GET['task_types']) : '';
if ($rawTaskTypes === '' && isset($_GET['task_type'])) {
    $rawTaskTypes = trim((string)$_GET['task_type']);
}
$ttFilterList = [];
if ($rawTaskTypes !== '' && strtolower($rawTaskTypes) !== 'all') {
    $ttFilterList = array_values(array_filter(
        array_map('trim', explode(',', $rawTaskTypes)),
        function ($item) { return $item !== ''; }
    ));
}

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
    error_log('[metrics api_comparison] db_connect_failed: ' . $e->getMessage());
    echo json_encode(['error' => 'db_unavailable']);
    exit;
}

// SQL view уже ограничивает 90 дней. Дополнительно фильтруем по period —
// на случай если view расширят. Чтобы SQL имел смысл, view считается по 90d,
// клиент сам решит какие rows включить исходя из period.
$sql = "
    SELECT
        model,
        task_type,
        total_runs,
        successful_runs,
        success_rate,
        avg_duration_sec,
        avg_cost_usd,
        stable_runs,
        stability_rate,
        mqi_preview
    FROM metrics_by_task_type_v
    WHERE 1=1
";
$params = [];
if ($model !== '') {
    $sql .= ' AND model = :model';
    $params[':model'] = $model;
}
if (!empty($ttFilterList)) {
    // multi-select: WHERE task_type = ANY(...)
    $sql .= ' AND task_type = ANY(string_to_array(:tt_list, :tt_sep))';
    $params[':tt_list'] = implode(',', $ttFilterList);
    $params[':tt_sep']  = ',';
}
$sql .= ' ORDER BY model ASC, task_type ASC';

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rawRows = $stmt->fetchAll();

// Считаем агрегаты по task_type (без фильтра по task_types — для badge в chips).
// С model-фильтром сохраняем, чтобы badge соответствовал выбранной модели.
$countSql = '
    SELECT task_type, SUM(total_runs)::bigint AS runs
    FROM metrics_by_task_type_v
    WHERE 1=1
';
$countParams = [];
if ($model !== '') {
    $countSql .= ' AND model = :model';
    $countParams[':model'] = $model;
}
$countSql .= ' GROUP BY task_type ORDER BY task_type ASC';

$countStmt = $pdo->prepare($countSql);
$countStmt->execute($countParams);
$taskTypeCounts = [];
foreach ($countStmt->fetchAll() as $cr) {
    $taskTypeCounts[(string)$cr['task_type']] = (int)$cr['runs'];
}

// Нормализация типов + лёгкая фильтрация по period (на базе total_runs не выйдет,
// period влияет только на freshness метрик; у нас окно в view фиксировано 90d;
// здесь period применим для отображения KPI summary рядом с таблицей).
$rows = [];
$taskTypesSet = [];
$modelsSet = [];
$kpiTotalRuns = 0;
$kpiTotalSuccess = 0;
$kpiTotalCost = 0.0;
$kpiSumSuccessRate = 0.0;
$kpiSumMqi = 0.0;
$kpiRatCount = 0;

foreach ($rawRows as $r) {
    $row = [
        'model'            => $r['model'],
        'task_type'        => $r['task_type'],
        'total_runs'       => (int)$r['total_runs'],
        'successful_runs'  => (int)$r['successful_runs'],
        'success_rate'     => $r['success_rate'] !== null ? (float)$r['success_rate'] : 0.0,
        'avg_duration_sec' => $r['avg_duration_sec'] !== null ? (float)$r['avg_duration_sec'] : null,
        'avg_cost_usd'     => $r['avg_cost_usd'] !== null ? (float)$r['avg_cost_usd'] : null,
        'stable_runs'      => (int)$r['stable_runs'],
        'stability_rate'   => $r['stability_rate'] !== null ? (float)$r['stability_rate'] : 0.0,
        'mqi_preview'      => $r['mqi_preview'] !== null ? (float)$r['mqi_preview'] : 0.0,
    ];
    $rows[] = $row;
    $taskTypesSet[$r['task_type']] = true;
    $modelsSet[$r['model']] = true;
    $kpiTotalRuns += $row['total_runs'];
    $kpiTotalSuccess += $row['successful_runs'];
    if ($row['avg_cost_usd'] !== null) {
        $kpiTotalCost += $row['avg_cost_usd'] * $row['total_runs'];
    }
    $kpiSumSuccessRate += $row['success_rate'];
    $kpiSumMqi += $row['mqi_preview'];
    $kpiRatCount++;
}

$taskTypes = array_keys($taskTypesSet);
sort($taskTypes);
$models = array_keys($modelsSet);
sort($models);

$kpis = [
    'total_runs'          => $kpiTotalRuns,
    'total_successful'    => $kpiTotalSuccess,
    'models_count'        => count($models),
    'task_types_count'    => count($taskTypes),
    'avg_success_rate'    => $kpiRatCount > 0 ? round($kpiSumSuccessRate / $kpiRatCount, 4) : 0,
    'avg_mqi_preview'     => $kpiRatCount > 0 ? round($kpiSumMqi / $kpiRatCount, 4) : 0,
    'total_cost_usd'      => round($kpiTotalCost, 4),
];

echo json_encode([
    'period_days'        => $days,
    'model_filter'       => $model,
    'task_types_filter'  => $ttFilterList,
    'task_type_filter'   => $rawTaskTypes, // legacy / debug
    'task_types_count'   => count($ttFilterList) > 0 ? count($ttFilterList) : count($taskTypeCounts),
    'rows'               => $rows,
    'task_types'         => $taskTypes,           // в выдаче (с учётом фильтра)
    'task_type_counts'   => $taskTypeCounts,      // ВСЕ task_types с run count для chips badges
    'models'             => $models,
    'kpis'               => $kpis,
    'generated_at'       => date('c'),
], JSON_UNESCAPED_UNICODE);
