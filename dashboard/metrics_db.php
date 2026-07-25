<?php
// Shared DB connection for dashboard endpoints.
declare(strict_types=1);

function metrics_db_connect(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO(
            'pgsql:host=127.0.0.1;port=5434;dbname=bizdnai',
            'bizdnai',
            'bizdnai',
            [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES => false,
            ]
        );
    }
    return $pdo;
}
