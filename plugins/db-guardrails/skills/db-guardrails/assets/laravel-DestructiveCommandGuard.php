<?php

declare(strict_types=1);

namespace App\Support;

use Closure;
use Illuminate\Console\Events\CommandStarting;
use RuntimeException;

/**
 * db-guardrails — layer 2 for Laravel.
 *
 * Aborts destructive artisan commands when the target connection is a real
 * MySQL/MariaDB database, unless ALLOW_DESTRUCTIVE=true is set in the
 * environment. Also fails closed when the config is cached (a cached config
 * can mask which connection a command will actually hit).
 *
 * Register in App\Providers\AppServiceProvider::boot():
 *
 *   use App\Support\DestructiveCommandGuard;
 *   use Illuminate\Console\Events\CommandStarting;
 *   use Illuminate\Support\Facades\Event;
 *
 *   Event::listen(
 *       CommandStarting::class,
 *       fn ($e) => (new DestructiveCommandGuard())->check($e),
 *   );
 */
class DestructiveCommandGuard
{
    /** @var list<string> */
    public const COMMANDS = [
        'migrate:fresh',
        'migrate:refresh',
        'migrate:reset',
        'migrate:rollback',
        'db:wipe',
    ];

    /**
     * @param  (Closure(): bool)|null  $configCachedCheck
     */
    public function __construct(
        private readonly ?Closure $configCachedCheck = null,
    ) {}

    public function check(CommandStarting $event): void
    {
        if (! in_array($event->command, self::COMMANDS, true)) {
            return;
        }

        if ($this->isConfigCached()) {
            throw new RuntimeException(
                'BLOCKED: config is cached; destructive commands refuse to run. Run `php artisan config:clear` first.'
            );
        }

        $connectionName = $event->input->getParameterOption('--database') ?: config('database.default');
        $driver = config("database.connections.{$connectionName}.driver");

        // SQLite (including the in-memory test database) is disposable; every
        // other driver — mysql, mariadb, pgsql, sqlsrv — is a real database
        // and is guarded. A null/unknown driver fails closed.
        if ($driver !== 'sqlite' && ! $this->isDestructiveAllowed()) {
            throw new RuntimeException(
                "BLOCKED: {$event->command} targets the '{$connectionName}' connection "
                .'('.($driver ?? 'unknown driver').'). '
                .'Set ALLOW_DESTRUCTIVE=true to override — only after confirming the connection is safe.'
            );
        }
    }

    private function isConfigCached(): bool
    {
        return $this->configCachedCheck !== null
            ? ($this->configCachedCheck)()
            : app()->configurationIsCached();
    }

    private function isDestructiveAllowed(): bool
    {
        $value = $_SERVER['ALLOW_DESTRUCTIVE'] ?? $_ENV['ALLOW_DESTRUCTIVE'] ?? getenv('ALLOW_DESTRUCTIVE');

        return $value === 'true';
    }
}
