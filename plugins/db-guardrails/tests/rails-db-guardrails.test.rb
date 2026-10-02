# frozen_string_literal: true

require "minitest/autorun"
require "open3"
require "rbconfig"

# Real Rake invocations, with a Rails-like environment/load_config graph and
# marker actions in place of database calls. No Rails install or database needed.
class RailsDbGuardrailsTest < Minitest::Test
  ASSET = File.expand_path("../skills/db-guardrails/assets/rails-db_guardrails.rb", __dir__)
  TASKS = %w[db:drop db:reset db:purge db:truncate_all db:schema:load
             db:structure:load db:test:prepare db:migrate:reset].freeze
  HARNESS = <<~'RUBY'
    gem "rake", ENV.fetch("GUARD_TEST_RAKE_VERSION") if ENV["GUARD_TEST_RAKE_VERSION"]
    require "rake"
    asset, order, requested, scenario, concurrency = ARGV
    Rake.application.options.always_multitask = true if concurrency == "always_multitask"
    Rake::Task.define_task("environment") do
      module Rails
        def self.env
          value = ENV.fetch("GUARD_TEST_ENV").dup
          def value.test?; self == "test"; end
          value
        end
      end
      puts "ENVIRONMENT BOOTED"
    end
    define_database_tasks = proc do
      Rake::Task.define_task("db:load_config" => "environment")
      Rake::Task.define_task("db:check_protected_environments" => "db:load_config")
      # Deliberately independent of environment: this marker must not be
      # dispatched concurrently with the guard, even while the app boots.
      Rake::Task.define_task("db:destructive_prerequisite") do
        puts "DESTRUCTIVE PREREQUISITE"
      end
      names = %w[db:drop db:reset db:purge db:truncate_all db:schema:load
                 db:structure:load db:test:prepare db:migrate:reset]
      names.each do |name|
        prereqs = if scenario == "destructive_prerequisite"
                    ["db:destructive_prerequisite"]
                  elsif name == "db:reset"
                    ["db:drop", "db:schema:load"]
                  elsif name == "db:migrate:reset"
                    ["db:drop", "db:migrate"]
                  else
                    ["db:load_config", "db:check_protected_environments"]
                  end
        task_class = concurrency == "multitask" ? Rake::MultiTask : Rake::Task
        task_class.define_task(name => prereqs) { puts "DESTRUCTIVE ACTION #{name}" }
      end
      Rake::Task.define_task("db:migrate" => "db:load_config") { puts "SAFE ACTION" }
      Rake::Task.define_task("test" => "environment") { puts "SAFE ACTION" }
    end
    load asset if order == "before"
    define_database_tasks.call
    load asset if order == "after"
    load asset if scenario == "reload"
    if scenario == "runtime_change"
      Rake::Task[requested].invoke
      Rake::Task[requested].reenable
      ENV["GUARD_TEST_ENV"] = "development"
      ENV.delete("ALLOW_DESTRUCTIVE")
      puts "SECOND INVOCATION"
    end
    if scenario == "different_task"
      Rake::Task["db:drop"].invoke
      ENV.delete("ALLOW_DESTRUCTIVE")
      puts "SECOND INVOCATION"
    end
    Rake::Task[requested].invoke
  RUBY

  def invoke(task, environment: "development", override: nil, order: "after", scenario: "normal", concurrency: "sequential")
    Open3.capture3({ "GUARD_TEST_ENV" => environment, "ALLOW_DESTRUCTIVE" => override },
                  RbConfig.ruby, "-e", HARNESS, ASSET, order, task, scenario, concurrency)
  end

  TASKS.each do |task|
    %w[before after].each do |order|
      define_method("test_#{task.tr(':', '_')}_blocked_#{order}_definitions") do
        stdout, stderr, status = invoke(task, order: order)
        refute status.success?, stdout + stderr
        assert_includes stdout, "ENVIRONMENT BOOTED"
        assert_includes stderr, "BLOCKED by db-guardrails"
        refute_includes stdout, "DESTRUCTIVE"
      end

      define_method("test_#{task.tr(':', '_')}_blocks_destructive_prerequisite_#{order}") do
        stdout, stderr, status = invoke(task, order: order, scenario: "destructive_prerequisite")
        refute status.success?, stdout + stderr
        assert_includes stderr, "BLOCKED by db-guardrails"
        refute_includes stdout, "DESTRUCTIVE"
      end

      define_method("test_#{task.tr(':', '_')}_test_environment_allowed_#{order}") do
        stdout, stderr, status = invoke(task, environment: "test", order: order)
        assert status.success?, stdout + stderr
        assert_includes stdout, "DESTRUCTIVE ACTION #{task}"
        assert_empty stderr
      end

      define_method("test_#{task.tr(':', '_')}_explicit_override_allowed_#{order}") do
        stdout, stderr, status = invoke(task, environment: "production", override: "true", order: order)
        assert status.success?, stdout + stderr
        assert_includes stdout, "DESTRUCTIVE ACTION #{task}"
        assert_empty stderr
      end
    end
  end

  def test_production_is_blocked_and_override_is_exact
    [nil, "false", "TRUE", "1"].each do |override|
      stdout, stderr, status = invoke("db:drop", environment: "production", override: override)
      refute status.success?, stdout + stderr
      assert_includes stderr, "destructive DB task in production"
      refute_includes stdout, "DESTRUCTIVE"
    end
  end

  def test_normal_migrations_and_tests_pass
    %w[db:migrate test].each do |task|
      %w[before after].each do |order|
        stdout, stderr, status = invoke(task, order: order)
        assert status.success?, stdout + stderr
        assert_includes stdout, "SAFE ACTION"
        assert_empty stderr
      end
    end
  end

  def test_environment_and_override_are_checked_at_invocation
    [{ environment: "test" }, { override: "true" }].each do |options|
      stdout, stderr, status = invoke("db:drop", **options, order: "before", scenario: "runtime_change")
      refute status.success?, stdout + stderr
      assert_includes stdout, "DESTRUCTIVE ACTION db:drop"
      assert_includes stderr, "BLOCKED by db-guardrails"
      refute_includes stdout.split("SECOND INVOCATION").last, "DESTRUCTIVE"
      assert_equal 1, stdout.scan("ENVIRONMENT BOOTED").length
    end
  end

  def test_override_does_not_carry_over_to_another_task
    stdout, stderr, status = invoke("db:purge", override: "true", scenario: "different_task")
    refute status.success?, stdout + stderr
    assert_includes stdout, "DESTRUCTIVE ACTION db:drop"
    assert_includes stderr, "BLOCKED by db-guardrails"
    refute_includes stdout.split("SECOND INVOCATION").last, "DESTRUCTIVE"
    assert_equal 1, stdout.scan("ENVIRONMENT BOOTED").length
  end

  def test_reloading_asset_keeps_guard_active
    stdout, stderr, status = invoke("db:drop", scenario: "reload")
    refute status.success?, stdout + stderr
    assert_includes stderr, "BLOCKED by db-guardrails"
    refute_includes stdout, "DESTRUCTIVE"
  end

  %w[always_multitask multitask].each do |concurrency|
    %w[before after].each do |order|
      define_method("test_#{concurrency}_blocks_before_dispatch_#{order}") do
        TASKS.each do |task|
          stdout, stderr, status = invoke(task, concurrency: concurrency, order: order,
                                         scenario: "destructive_prerequisite")
          refute status.success?, stdout + stderr
          assert_includes stderr, "BLOCKED by db-guardrails"
          refute_includes stdout, "DESTRUCTIVE"
        end
      end

      define_method("test_#{concurrency}_allowed_tasks_still_execute_#{order}") do
        [{ environment: "test" }, { override: "true" }].each do |options|
          TASKS.each do |task|
            stdout, stderr, status = invoke(task, **options, concurrency: concurrency,
                                           order: order, scenario: "destructive_prerequisite")
            assert status.success?, stdout + stderr
            assert_includes stdout, "DESTRUCTIVE PREREQUISITE"
            assert_includes stdout, "DESTRUCTIVE ACTION #{task}"
            assert_empty stderr
          end
        end
      end
    end
  end
end
