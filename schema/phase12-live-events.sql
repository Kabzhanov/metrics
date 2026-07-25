-- Phase 12: PostgreSQL -> WebSocket live metrics events (task #1176).
-- Safe to re-run: trigger names are replaced explicitly.

CREATE OR REPLACE FUNCTION notify_metric_event() RETURNS trigger AS $$
DECLARE
    live_event_type TEXT;
    event_data JSON;
BEGIN
    IF TG_TABLE_NAME = 'run_events' THEN
        IF NEW.event_type IN ('task_started', 'task_completed', 'degradation_detected', 'tool_error') THEN
            live_event_type := NEW.event_type;
        ELSIF NEW.success IS FALSE OR NEW.error_code IS NOT NULL THEN
            live_event_type := 'tool_error';
        ELSE
            -- Routine tool/file events do not require a full dashboard refresh.
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'run_evaluations' THEN
        IF NEW.reopened IS TRUE
           OR NEW.build_passed IS FALSE
           OR NEW.tests_passed IS FALSE
           OR NEW.lint_passed IS FALSE
           OR NEW.acceptance_passed IS FALSE THEN
            live_event_type := 'degradation_detected';
        ELSE
            live_event_type := 'task_completed';
        END IF;
    ELSIF TG_TABLE_NAME = 'metrics_tasks' THEN
        IF TG_OP = 'INSERT' THEN
            live_event_type := 'task_started';
        ELSIF NEW.status IS NOT DISTINCT FROM OLD.status THEN
            RETURN NEW;
        ELSIF NEW.status = 'success' THEN
            live_event_type := 'task_completed';
        ELSIF NEW.status IN ('failed', 'interrupted', 'human_stop') THEN
            live_event_type := 'tool_error';
        ELSE
            RETURN NEW;
        END IF;
    ELSE
        RETURN NEW;
    END IF;

    event_data := row_to_json(NEW);
    PERFORM pg_notify(
        'bizdnai_metrics_events',
        json_build_object(
            'event_type', live_event_type,
            'data', event_data,
            'timestamp', CURRENT_TIMESTAMP
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS notify_run_events ON run_events;
CREATE TRIGGER notify_run_events
AFTER INSERT ON run_events
FOR EACH ROW EXECUTE FUNCTION notify_metric_event();

DROP TRIGGER IF EXISTS notify_run_evaluations ON run_evaluations;
CREATE TRIGGER notify_run_evaluations
AFTER INSERT OR UPDATE ON run_evaluations
FOR EACH ROW EXECUTE FUNCTION notify_metric_event();

DROP TRIGGER IF EXISTS notify_metrics_tasks ON metrics_tasks;
CREATE TRIGGER notify_metrics_tasks
AFTER INSERT OR UPDATE ON metrics_tasks
FOR EACH ROW EXECUTE FUNCTION notify_metric_event();
