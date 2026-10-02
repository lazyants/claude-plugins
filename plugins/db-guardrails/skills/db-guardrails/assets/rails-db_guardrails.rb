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
# Define placeholders so later task definitions keep the guard too, and put
# the guard FIRST so destructive prerequisites cannot run ahead of it.
# Boot the environment before checking Rails.env, which is not available yet
# when this file loads. The .rake placement keeps this out of server boot.

if defined?(Rake)
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

  unless Rake::Task.task_defined?("db_guardrails:block")
    Rake::Task.define_task("db_guardrails:block" => "environment") do
      unless Rails.env.test? || ENV["ALLOW_DESTRUCTIVE"] == "true"
        abort "BLOCKED by db-guardrails: destructive DB task in #{Rails.env}. " \
              "Set ALLOW_DESTRUCTIVE=true to override."
      end
    end
  end

  destructive_tasks.each do |name|
    task = Rake::Task.define_task(name)
    task.prerequisites.unshift("db_guardrails:block") unless task.prerequisites.include?("db_guardrails:block")
  end
end
