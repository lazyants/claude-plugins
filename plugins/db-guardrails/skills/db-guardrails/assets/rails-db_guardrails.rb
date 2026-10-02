# frozen_string_literal: true
#
# db-guardrails — layer 2 for Rails.
#
# Aborts destructive `db:*` rake tasks outside the test environment unless
# ALLOW_DESTRUCTIVE=true is set.
#
# Install: copy this file to `lib/tasks/db_guardrails.rake`.
#
# Rails loads lib/tasks before invoking any task. An initializer is too late:
# Rake has already copied the invoking task's prerequisites by then.
# Define placeholders so later task definitions keep the guard too. Each
# guarded task completes the check before dispatching ANY prerequisites,
# including under `rake --multitask` or Rake::MultiTask.
# Boot the environment before checking Rails.env, which is not available yet
# when this file loads. Only boot is cached: the safety check runs on every
# execution, even when just the destructive task is re-enabled. The .rake
# placement keeps this out of server boot.

if defined?(Rake)
  module DbGuardrailsTaskGuard
    def invoke_prerequisites(task_args, invocation_chain)
      Rake::Task["environment"].invoke
      unless Rails.env.test? || ENV["ALLOW_DESTRUCTIVE"] == "true"
        abort "BLOCKED by db-guardrails: destructive DB task in #{Rails.env}. " \
              "Set ALLOW_DESTRUCTIVE=true to override."
      end
      super(task_args, invocation_chain)
    end
  end

  destructive_tasks = %w[
    db:drop
    db:reset
    db:purge
    db:truncate_all
    db:schema:load
    db:structure:load
    db:test:prepare
    db:migrate:reset
  ]

  destructive_tasks.each do |name|
    task = Rake::Task.define_task(name)
    task.singleton_class.prepend(DbGuardrailsTaskGuard)
  end
end
