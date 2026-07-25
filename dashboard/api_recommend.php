<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: public, max-age=60');

require_once __DIR__ . '/metrics_db.php';

$task_type = isset($_GET['task_type']) ? (string)$_GET['task_type'] : '';

$valid_task_types = ['architecture', 'feature', 'bugfix', 'refactoring', 'testing', 'documentation', 'frontend', 'backend', 'integration', 'devops', 'content_generation', 'research'];

if ($task_type === '' || !in_array($task_type, $valid_task_types, true)) {
    $all_types = get_all_task_types_with_data();
    echo json_encode([
        'status' => 'ok',
        'task_type' => null,
        'available_task_types' => $all_types,
        'hint' => 'Передай ?task_type=bugfix чтобы получить рекомендацию',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

$rows = get_models_by_task_type($task_type, 30);

if (empty($rows)) {
    echo json_encode([
        'status' => 'insufficient_data',
        'task_type' => $task_type,
        'message' => "Нет данных по типу '{$task_type}' за последние 30 дней. Попробуй позже или выбери другой тип.",
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

$scored = score_models($rows, $task_type);
$best = $scored[0];
$alternatives = array_slice($scored, 1, 4);

$reasoning = build_reasoning($best, $alternatives, $task_type);

echo json_encode([
    'status' => 'ok',
    'task_type' => $task_type,
    'task_type_label' => task_type_label($task_type),
    'best' => $best,
    'alternatives' => $alternatives,
    'reasoning' => $reasoning,
    'sample_size_total' => array_sum(array_column($rows, 'total_runs')),
    'note_when_choosing' => build_choosing_note($best, $task_type),
], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);


function get_all_task_types_with_data(): array {
    $pdo = metrics_db_connect();
    $stmt = $pdo->query("
        SELECT t.task_type, COUNT(*) AS n
        FROM task_runs tr
        LEFT JOIN tasks t ON t.id = tr.task_id
        WHERE tr.started_at > now() - interval '90 days' AND t.task_type IS NOT NULL
        GROUP BY t.task_type
        ORDER BY n DESC
    ");
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $out = [];
    foreach ($rows as $r) {
        $out[] = ['task_type' => $r['task_type'], 'label' => task_type_label((string)$r['task_type']), 'runs' => (int)$r['n']];
    }
    return $out;
}

function get_models_by_task_type(string $task_type, int $period_days): array {
    $pdo = metrics_db_connect();
    $stmt = $pdo->prepare("
        SELECT tr.model,
               COUNT(*) AS total_runs,
               COUNT(*) FILTER (WHERE tr.status='success') AS successful_runs,
               ROUND(AVG(CASE WHEN tr.status='success' THEN 1.0 ELSE 0.0 END)::numeric, 4) AS success_rate,
               ROUND(AVG(NULLIF(tr.duration_sec, 0))::numeric, 1) AS avg_duration_sec,
               ROUND(AVG(NULLIF(tr.cost, 0))::numeric, 4) AS avg_cost_usd,
               ROUND(AVG(NULLIF(tr.input_tokens, 0) + NULLIF(tr.output_tokens, 0))::numeric, 0) AS avg_tokens,
               COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false)) AS stable_runs
        FROM task_runs tr
        LEFT JOIN tasks t ON t.id = tr.task_id
        LEFT JOIN run_evaluations re ON re.run_id = tr.id
        WHERE t.task_type = :tt
          AND tr.started_at > now() - (:pd || ' days')::interval
        GROUP BY tr.model
        HAVING COUNT(*) >= 3
    ");
    $stmt->execute(['tt' => $task_type, 'pd' => (string)$period_days]);
    return $stmt->fetchAll(PDO::FETCH_ASSOC);
}

function score_models(array $rows, string $task_type): array {
    $min_cost = PHP_FLOAT_MAX;
    $min_duration = PHP_FLOAT_MAX;
    foreach ($rows as $r) {
        if ((float)$r['avg_cost_usd'] > 0 && (float)$r['avg_cost_usd'] < $min_cost) $min_cost = (float)$r['avg_cost_usd'];
        if ((float)$r['avg_duration_sec'] > 0 && (float)$r['avg_duration_sec'] < $min_duration) $min_duration = (float)$r['avg_duration_sec'];
    }

    $scored = [];
    foreach ($rows as $r) {
        $success = (float)$r['success_rate'];
        $stability = (int)$r['total_runs'] > 0 ? (int)$r['stable_runs'] / (int)$r['total_runs'] : 0.0;
        // Cost ratio: free/cheapest = 1.0, expensive = lower. NULL/0 cost = MAX (free = best).
        $model_cost = (float)$r['avg_cost_usd'];
        if ($model_cost <= 0) {
            $cost_ratio = 1.0;  // free → max score
        } elseif ($min_cost > 0 && $min_cost <= $model_cost) {
            $cost_ratio = $min_cost / $model_cost;  // 1.0 for cheapest, < 1.0 for more expensive
        } else {
            $cost_ratio = 0.0;  // fallback (shouldn't happen)
        }
        // Speed ratio: fastest = 1.0, slower = lower
        $model_dur = (float)$r['avg_duration_sec'];
        $speed_ratio = ($min_duration > 0 && $model_dur > 0) ? $min_duration / $model_dur : 0.0;

        // Composite: 50% success, 20% cost, 20% speed, 10% stability
        $score = $success * 0.5
               + $cost_ratio * 0.2
               + $speed_ratio * 0.2
               + $stability * 0.1;

        $r['success_rate'] = round($success, 4);
        $r['stability_rate'] = round($stability, 4);
        $r['composite_score'] = round($score, 4);
        $r['avg_duration_sec'] = (float)$r['avg_duration_sec'];
        $r['avg_cost_usd'] = (float)$r['avg_cost_usd'];
        $r['total_runs'] = (int)$r['total_runs'];
        $r['successful_runs'] = (int)$r['successful_runs'];
        $r['stable_runs'] = (int)$r['stable_runs'];
        $r['avg_tokens'] = (int)$r['avg_tokens'];
        $r['best_at'] = describe_strength($r, $task_type);

        $scored[] = $r;
    }

    usort($scored, function($a, $b) {
        return $b['composite_score'] <=> $a['composite_score'];
    });

    return $scored;
}

function describe_strength(array $r, string $task_type): string {
    $parts = [];
    if ((float)$r['success_rate'] >= 0.9) {
        $parts[] = 'высокая надёжность';
    }
    if ((float)$r['avg_cost_usd'] > 0 && (float)$r['avg_cost_usd'] < 0.1) {
        $parts[] = 'дешёвая';
    } elseif ((float)$r['avg_cost_usd'] === 0.0) {
        $parts[] = 'бесплатная (локальная)';
    }
    if ((float)$r['avg_duration_sec'] > 0 && (float)$r['avg_duration_sec'] < 30) {
        $parts[] = 'быстрая';
    } elseif ((float)$r['avg_duration_sec'] >= 30 && (float)$r['avg_duration_sec'] < 60) {
        $parts[] = 'средняя скорость';
    }
    if ((float)$r['stability_rate'] >= 0.9) {
        $parts[] = 'стабильная';
    }
    return implode(', ', $parts) ?: 'без особенностей';
}

function build_reasoning(array $best, array $alternatives, string $task_type): string {
    $label = task_type_label($task_type);
    $parts = [];

    $parts[] = "**{$best['model']}** — лучший выбор для **«{$label}»**.";

    $reasons = [];
    $reasons[] = sprintf('%d%% задач выполнены успешно (из %d всего)', (int)round((float)$best['success_rate'] * 100), $best['total_runs']);

    if ((float)$best['avg_cost_usd'] > 0) {
        $reasons[] = sprintf('средняя стоимость задачи: $%s', number_format((float)$best['avg_cost_usd'], 3));
    } else {
        $reasons[] = 'бесплатно (локальный запуск)';
    }
    if ((float)$best['avg_duration_sec'] > 0) {
        $reasons[] = sprintf('среднее время: %s сек', number_format((float)$best['avg_duration_sec'], 1));
    }

    $parts[] = '**Почему:** ' . implode(' · ', $reasons) . '.';

    if (!empty($alternatives)) {
        $parts[] = '';
        $parts[] = '**Альтернативы:**';
        foreach (array_slice($alternatives, 0, 3) as $alt) {
            $delta = ($best['avg_cost_usd'] > 0 && (float)$alt['avg_cost_usd'] > 0)
                ? sprintf('(в %.1fx дороже)', (float)$alt['avg_cost_usd'] / max((float)$best['avg_cost_usd'], 0.0001))
                : '';
            $parts[] = sprintf(
                "- **%s**: %.0f%% успех, $%s%s",
                $alt['model'],
                (float)$alt['success_rate'] * 100,
                number_format((float)$alt['avg_cost_usd'], 3),
                $delta
            );
        }
    }

    return implode("\n", $parts);
}

function build_choosing_note(array $best, string $task_type): string {
    $best_at = $best['best_at'] ?? '';
    $samples = $best['total_runs'];
    $confidence = match (true) {
        $samples >= 100 => 'высокая (≥100 задач)',
        $samples >= 30  => 'средняя (≥30 задач)',
        $samples >= 10  => 'низкая (≥10 задач)',
        default          => 'очень низкая (<10 задач)',
    };
    return sprintf('Уверенность: %s. Сильные стороны: %s.', $confidence, $best_at);
}

function task_type_label(string $tt): string {
    $labels = [
        'architecture' => 'Архитектура',
        'feature' => 'Новая функциональность',
        'bugfix' => 'Исправление бага',
        'refactoring' => 'Рефакторинг',
        'testing' => 'Тестирование',
        'documentation' => 'Документация',
        'frontend' => 'Frontend',
        'backend' => 'Backend',
        'integration' => 'Интеграция',
        'devops' => 'DevOps',
        'content_generation' => 'Генерация контента',
        'research' => 'Исследование',
    ];
    return $labels[$tt] ?? $tt;
}
