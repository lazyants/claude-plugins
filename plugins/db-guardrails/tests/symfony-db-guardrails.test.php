<?php

declare(strict_types=1);

// The guard only depends on these event contracts. Stub them so the asset's
// decisions are exercised without installing an application or Doctrine.
namespace Symfony\Component\EventDispatcher {
    interface EventSubscriberInterface
    {
        public static function getSubscribedEvents(): array;
    }
}

namespace Symfony\Component\Console {
    final class ConsoleEvents
    {
        public const COMMAND = 'console.command';
    }
}

namespace Symfony\Component\Console\Event {
    final class ConsoleCommandEvent
    {
        public function __construct(private ?string $name, private array $options = []) {}

        public function getCommand(): ?object
        {
            return $this->name === null ? null : new class($this->name) {
                public function __construct(private string $name) {}
                public function getName(): string { return $this->name; }
            };
        }

        public function getInput(): object
        {
            return new class($this->options) {
                public function __construct(private array $options) {}
                public function getOption(string $name): mixed { return $this->options[$name] ?? false; }
            };
        }
    }
}

namespace {
    require __DIR__ . '/../skills/db-guardrails/assets/symfony-DestructiveCommandGuard.php';

    use App\Console\DestructiveCommandGuard;
    use Symfony\Component\Console\Event\ConsoleCommandEvent;

    $cases = 0;
    function check(?string $name, array $options, bool $blocked, ?string $env = 'dev', ?string $override = null): void
    {
        global $cases;
        unset($_SERVER['APP_ENV'], $_ENV['APP_ENV'], $_SERVER['ALLOW_DESTRUCTIVE'], $_ENV['ALLOW_DESTRUCTIVE']);
        putenv('APP_ENV');
        putenv('ALLOW_DESTRUCTIVE');
        if ($env !== null) { $_SERVER['APP_ENV'] = $env; }
        if ($override !== null) { $_SERVER['ALLOW_DESTRUCTIVE'] = $override; }
        $caught = null;
        try {
            (new DestructiveCommandGuard())->onCommand(new ConsoleCommandEvent($name, $options));
        } catch (RuntimeException $exception) {
            $caught = $exception;
        }
        if (($caught !== null) !== $blocked) {
            throw new RuntimeException(sprintf('Unexpected decision for %s %s env=%s override=%s',
                $name ?? '(null)', json_encode($options), $env ?? '(unset)', $override ?? '(unset)'));
        }
        if ($caught !== null && ! str_contains($caught->getMessage(), 'BLOCKED by db-guardrails')) {
            throw new RuntimeException('Guard threw an unrelated error: ' . $caught->getMessage());
        }
        ++$cases;
    }

    if (DestructiveCommandGuard::getSubscribedEvents() !== ['console.command' => 'onCommand']) {
        throw new RuntimeException('Guard does not subscribe to console.command');
    }
    check(null, [], false);
    check('cache:clear', [], false);
    check('doctrine:database:create', [], false);
    foreach (['doctrine:database:drop', 'doctrine:schema:drop', 'doctrine:fixtures:load'] as $name) {
        check($name, [], true);
        check($name, [], true, null);
        check($name, [], true, 'prod');
        check($name, [], false, 'test');
        check($name, [], false, 'dev', 'true');
        foreach (['1', 'TRUE', 'false', 'yes', ''] as $override) {
            check($name, [], true, 'dev', $override);
        }
    }
    check('doctrine:fixtures:load', ['append' => true], false);
    check('doctrine:fixtures:load', ['append' => false], true);
    check('doctrine:fixtures:load', ['append' => 'false'], true); // never exempt a malformed value
    check('doctrine:fixtures:load', ['group' => ['one']], true);
    check('doctrine:fixtures:load', ['purge-with-truncate' => true], true);
    check('doctrine:schema:update', [], false);
    check('doctrine:schema:update', ['force' => false, 'dump-sql' => true], false);
    check('doctrine:schema:update', ['force' => true], true);
    check('doctrine:schema:update', ['force' => true, 'dump-sql' => true], true);
    check('doctrine:schema:update', ['force' => true], false, 'test');
    check('doctrine:schema:update', ['force' => true], false, 'prod', 'true');

    // The fallback sources are used when PHP has not populated $_SERVER.
    unset($_SERVER['APP_ENV'], $_SERVER['ALLOW_DESTRUCTIVE']);
    $_ENV['APP_ENV'] = 'test';
    (new DestructiveCommandGuard())->onCommand(new ConsoleCommandEvent('doctrine:fixtures:load'));
    unset($_ENV['APP_ENV']);
    putenv('APP_ENV=dev');
    putenv('ALLOW_DESTRUCTIVE=true');
    (new DestructiveCommandGuard())->onCommand(new ConsoleCommandEvent('doctrine:schema:update', ['force' => true]));
    putenv('APP_ENV');
    putenv('ALLOW_DESTRUCTIVE');
    printf("PASS: Symfony guard (%d command decisions plus environment fallbacks)\n", $cases);
}
