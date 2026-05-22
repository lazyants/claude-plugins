<?php

declare(strict_types=1);

namespace App\Console;

use RuntimeException;
use Symfony\Component\Console\ConsoleEvents;
use Symfony\Component\Console\Event\ConsoleCommandEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

/**
 * db-guardrails — layer 2 for Symfony.
 *
 * Aborts destructive Doctrine console commands unless ALLOW_DESTRUCTIVE=true.
 * Only the `test` environment is exempt — it targets a throwaway database.
 * `dev` is NOT exempt: the incident this plugin exists for was a development
 * database wipe.
 *
 * Install: place at `src/Console/DestructiveCommandGuard.php`. With Symfony's
 * default autoconfiguration, implementing EventSubscriberInterface is enough —
 * the service is registered automatically. Without autoconfigure, register it
 * explicitly in config/services.yaml:
 *
 *     services:
 *         App\Console\DestructiveCommandGuard:
 *             tags: ['kernel.event_subscriber']
 *
 * Verify it is active with:
 *     php bin/console debug:event-dispatcher console.command
 * DestructiveCommandGuard must appear in the listener list.
 */
final class DestructiveCommandGuard implements EventSubscriberInterface
{
    /** @var list<string> */
    private const COMMANDS = [
        'doctrine:database:drop',
        'doctrine:schema:drop',
    ];

    public static function getSubscribedEvents(): array
    {
        return [ConsoleEvents::COMMAND => 'onCommand'];
    }

    public function onCommand(ConsoleCommandEvent $event): void
    {
        $name = $event->getCommand()?->getName();

        if (! in_array($name, self::COMMANDS, true)) {
            return;
        }

        $env = $_SERVER['APP_ENV'] ?? $_ENV['APP_ENV'] ?? getenv('APP_ENV') ?: 'prod';
        if ($env === 'test') {
            return;
        }

        $allow = $_SERVER['ALLOW_DESTRUCTIVE'] ?? $_ENV['ALLOW_DESTRUCTIVE'] ?? getenv('ALLOW_DESTRUCTIVE');
        if ($allow === 'true') {
            return;
        }

        throw new RuntimeException(sprintf(
            'BLOCKED by db-guardrails: "%s" in APP_ENV=%s. Set ALLOW_DESTRUCTIVE=true to override.',
            $name,
            $env,
        ));
    }
}
