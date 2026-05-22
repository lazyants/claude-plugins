# frozen_string_literal: true
#
# db-guardrails — layer 2 for Rails.
#
# Aborts destructive `db:*` rake tasks outside the test environment unless
# ALLOW_DESTRUCTIVE=true is set.
#
# Install: place this file at `config/initializers/db_guardrails.rb`.
#
# Why this works: a destructive task such as `rails db:drop` depends on the
# `environment` task; initializers run while `environment` executes. By then
# the `db:*` tasks are already defined (Rake loaded them before booting the
# app), so enhancing them with a guard prerequisite is safe and ordering-
# independent. `defined?(Rake)` keeps this dormant for `rails server` etc.

if defined?(Rake) && !Rails.env.test? && ENV["ALLOW_DESTRUCTIVE"] != "true"
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
    Rake::Task.define_task("db_guardrails:block") do
      abort "BLOCKED by db-guardrails: destructive DB task in #{Rails.env}. " \
            "Set ALLOW_DESTRUCTIVE=true to override."
    end
  end

  destructive_tasks.each do |name|
    Rake::Task[name].enhance(["db_guardrails:block"]) if Rake::Task.task_defined?(name)
  end
end
