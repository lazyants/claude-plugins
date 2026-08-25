#!/usr/bin/env ruby
# frozen_string_literal: true
#
# Gate: every SKILL.md frontmatter in this repo satisfies the Agent Skills length
# caps -- name 1-64 characters, description 1-1024, compatibility 1-500 when
# present (https://agentskills.io/specification).
#
# WHY A REAL PARSER IS THE ROOT OF TRUST. The cap applies to the YAML *value*,
# not to the source text, and in this repo the two already differ: several
# shipped skills use block scalars whose raw bytes run longer than the string a
# parser produces. A dependency-free hand-rolled reader was drafted for this gate
# and rejected twice, because each review round turned up a fresh class of valid
# YAML it silently UNDER-measured -- aliases, block scalars with internal blank
# lines, a `|` block ending at the closing delimiter, `|+` keep-chomping, NBSP
# that JS trim() eats and YAML does not, duplicate keys, and a `description:` at
# column zero inside a flow mapping. Every one of those is a false PASS on a file
# that genuinely violates the cap, which is worse than no gate: it converts an
# unknown into a false assurance. Psych is Ruby stdlib and already a hard CI
# dependency of this repo (.github/workflows/enduser-handbook.yml), so shelling
# out to it makes the whole class disappear rather than shrinking it.
#
# Run: ruby tests/skill-frontmatter-limits.test.rb

require "rbconfig"
require "yaml"
require "date"
require "tmpdir"
require "fileutils"

# Loaded at the top level, never inside a rescue. If `date` were missing, the
# resulting NameError must surface as a gate bug -- an earlier draft caught it in
# the per-file rescue and reported it as a YAML parse error on every file, which
# is exactly the "fail closed for the wrong reason" shape this gate is about.
PERMITTED_CLASSES = [Date, Time].freeze

# name and description are required; compatibility is optional but capped when
# present. Charset and hyphen-position rules for `name` are deliberately NOT
# checked here -- this is a length gate, and a charset check has a different
# failure mode and belongs in its own change.
CAPS = { "name" => 64, "description" => 1024, "compatibility" => 500 }.freeze
REQUIRED_FIELDS = %w[name description].freeze

# The newline before the closing `---` is INSIDE the capture group, and that is
# load-bearing: dropping it changes block-scalar chomping, so a `|` block that
# ends immediately before the delimiter measures one character short. The
# previous attempt at this gate shipped the dropped-newline form AND verified it
# against a harness that used the same regex -- two copies of one mistake
# agreeing, which is not corroboration. Its "matching exactly" differential
# reported 783 for literary-translator where a correct slice gives 784.
#
# `\r?` tolerates CRLF, matching this repo's other frontmatter reader
# (plugins/enduser-handbook/skills/enduser-handbook/assets/lib/chapter-paths.mjs).
# `(?:\n|\z)` accepts a file that ends at the closing delimiter with no body.
FRONTMATTER = /\A---\r?\n(.*?\r?\n)---\r?(?:\n|\z)/m

# Raised for anything about a file's frontmatter that cannot be measured. Always
# names the construct, so widening the accepted subset is a deliberate act.
class FrontmatterError < StandardError; end

# Raised when the corpus itself cannot be walked or read. Never downgraded to a
# smaller corpus -- a check that iterates zero times prints exactly what a
# passing one prints.
class TraversalError < StandardError; end

# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def slice_frontmatter(src)
  match = FRONTMATTER.match(src)
  return match[1] if match

  # Name the cause rather than emitting a generic "no frontmatter". Each of
  # these is rejected on purpose: the spec puts the frontmatter at the very
  # start of the file and closes it with `---`.
  reason =
    if src.start_with?("\uFEFF")
      "file starts with a UTF-8 BOM before the opening '---'"
    elsif src.start_with?("\n", "\r\n")
      "file starts with a blank line before the opening '---'"
    elsif src.match?(/\A---\r?\n.*?\r?\n\.\.\.\r?(?:\n|\z)/m)
      "frontmatter is closed by '...' instead of '---'"
    else
      "no '---' frontmatter block at the start of the file"
    end
  raise FrontmatterError, reason
end

# Returns [problems, lengths]. `problems` is a list of human-readable strings,
# empty when the file passes.
def measure_frontmatter(src)
  frontmatter = slice_frontmatter(src)

  document =
    begin
      YAML.safe_load(frontmatter, permitted_classes: PERMITTED_CLASSES, aliases: true)
    rescue Psych::Exception => e
      # Psych::Exception is the common ancestor of SyntaxError, DisallowedClass
      # and BadAlias on Psych 3.1, and of AliasesNotEnabled on Psych 5. Naming
      # the subclasses individually would raise NameError on Psych 3.1, where
      # AliasesNotEnabled does not exist. Anything that is NOT a
      # Psych::Exception is a bug in this gate and is left to crash loudly.
      raise FrontmatterError, "frontmatter is not parseable YAML (#{e.class}: #{e.message.lines.first.to_s.strip})"
    end

  # Psych parses `name description` (a missing colon -- an ordinary typo) as the
  # plain String "name description". Ruby's String#[] is a SUBSTRING lookup, so
  # document["description"] would return "description" and this gate would
  # report a tidy length of 11 on frontmatter that no real loader accepts.
  unless document.is_a?(Hash)
    raise FrontmatterError, "frontmatter parses to #{document.class}, not a mapping"
  end

  problems = []
  lengths = {}

  CAPS.each do |field, cap|
    unless document.key?(field)
      problems << "#{field}: required field is absent" if REQUIRED_FIELDS.include?(field)
      next
    end

    value = document[field]
    unless value.is_a?(String)
      # to_s coercion would silently measure something the spec does not
      # describe: `description: 42` must not pass as a length-2 description.
      problems << "#{field}: value is #{value.class}, not a String"
      next
    end

    # String#length counts code points, which is what the spec's "characters"
    # means and what the reference validator's Python len() counts. A byte
    # count would over-measure every non-ASCII description.
    length = value.length
    lengths[field] = length
    if length < 1
      problems << "#{field}: length #{length} is below the minimum of 1"
    elsif length > cap
      problems << "#{field}: length #{length} exceeds the maximum of #{cap}"
    end
  end

  [problems, lengths]
end

# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------

# Dir.children raises SystemCallError (ENOENT, EACCES, ENOTDIR, ...) instead of
# quietly returning a shorter list. Dir.glob does NOT: over a directory the
# process cannot read it returns [] with no exception, so an unreadable plugin
# subtree would shrink the enumerated and the measured count in lockstep and
# every consistency check would still pass. That is the failure this gate must
# not have, so nothing here globs.
def list_directory(path)
  Dir.children(path)
rescue SystemCallError => e
  raise TraversalError, "cannot list #{path} (#{e.class})"
end

# lstat, never stat: a symlink must be visible AS a symlink so it can be refused
# by name rather than silently followed.
def lstat_directory_entry(path)
  File.lstat(path)
rescue SystemCallError => e
  raise TraversalError, "cannot stat #{path} (#{e.class})"
end

# Resolve a path under the repo root, refusing a symlink at EVERY component
# rather than only the last one. File.lstat declines to follow just the final
# component, so a symlinked `plugins` or `.claude` would be followed silently
# and the gate would measure a redirected tree while reporting it under the real
# labels -- the wrong corpus, which is precisely what the symlink policy exists
# to fail closed on. Only components BELOW repo_root are checked: the checkout
# itself may legitimately sit under symlinked ancestors (/var on macOS, for one).
def require_unlinked_path(repo_root, relative)
  path = repo_root
  walked = []
  relative.split("/").each do |component|
    path = File.join(path, component)
    walked << component
    next unless lstat_directory_entry(path).symlink?

    raise TraversalError, "#{walked.join('/')} is a symlink; this gate does not follow symlinks"
  end
  path
end

# A root this gate is about to walk must be a real directory it owns, reached
# through a chain that contains no symlink.
def require_own_directory(repo_root, relative)
  path = require_unlinked_path(repo_root, relative)
  raise TraversalError, "#{relative} is not a directory" unless File.lstat(path).directory?

  path
end

# Every SKILL.md at ANY depth under `root`, sorted. The walk is recursive on
# purpose: a fixed-depth walk skips plugins/<p>/skills/outer/nested/SKILL.md
# while leaving every count self-consistent. Over-coverage costs at most a false
# RED on a file no loader reads; under-coverage is the silent failure.
def skill_files_under(root)
  found = []
  pending = [root]

  until pending.empty?
    directory = pending.shift
    list_directory(directory).sort.each do |entry|
      path = File.join(directory, entry)
      # lstat, not stat: a symlink is not followed. A symlinked skill directory
      # would otherwise either be walked twice or loop forever, so it is
      # refused by name instead of being silently skipped.
      stat = lstat_directory_entry(path)

      if stat.symlink?
        raise TraversalError, "#{path} is a symlink; this gate does not follow symlinks"
      elsif stat.directory?
        pending << path
      elsif entry == "SKILL.md"
        found << path
      end
    end
  end

  found.sort
end

# The two roots the issue scopes this gate to. Note the plugin walk is rooted at
# plugins/<p>/skills and never at plugins/ itself: a SKILL.md elsewhere under a
# plugin is not a registered skill.
def skill_roots(repo_root)
  roots = { ".claude/skills" => require_own_directory(repo_root, ".claude/skills") }

  plugins_dir = require_unlinked_path(repo_root, "plugins")
  list_directory(plugins_dir).sort.each do |entry|
    plugin_dir = File.join(plugins_dir, entry)
    plugin_stat = lstat_directory_entry(plugin_dir)
    if plugin_stat.symlink?
      raise TraversalError, "plugins/#{entry} is a symlink; this gate does not follow symlinks"
    end
    # An ordinary file directly under plugins/ (a README, say) is not a plugin.
    # Probing <file>/skills would raise ENOTDIR and block an unrelated edit.
    next unless plugin_stat.directory?

    skills_dir = File.join(plugin_dir, "skills")
    skills_stat =
      begin
        File.lstat(skills_dir)
      rescue Errno::ENOENT
        # A plugin that ships no skills at all is legitimate. Detecting that a
        # plugin LOST a skills tree it used to have is deliberately NOT this
        # gate's job: it is a different defect class (a plugin published without
        # its declared skill), it is outside the frozen scope of a
        # frontmatter-LENGTH gate, and three review rounds established that no
        # honest version of it exists here. `skills` is optional in a manifest
        # AND may be a string or an array, so a manifest cannot distinguish
        # "never had skills" from "lost them"; the only mechanism that could is
        # a hand-maintained list of expected roots, which freezes exactly what
        # it is supposed to detect. Requirement 3's zero-iteration blindness is
        # covered by the per-root floors below, which is what it actually asks.
        next
      rescue SystemCallError => e
        raise TraversalError, "cannot stat #{skills_dir} (#{e.class})"
      end

    if skills_stat.symlink?
      raise TraversalError, "plugins/#{entry}/skills is a symlink; this gate does not follow symlinks"
    end
    unless skills_stat.directory?
      raise TraversalError, "plugins/#{entry}/skills exists but is not a directory"
    end

    roots["plugins/#{entry}/skills"] = skills_dir
  end

  roots
end

# Returns { label => [paths] }. Raises TraversalError on any corpus-integrity
# failure: an empty root, or a skill directory that has lost its SKILL.md.
def collect_corpus(repo_root)
  roots = skill_roots(repo_root)
  corpus = {}

  roots.each do |label, dir|
    files = skill_files_under(dir)

    # Per-root floor. A total-only floor would let the shipped plugins/ tree go
    # empty while the internal tree kept the number plausible.
    raise TraversalError, "root #{label} contains no SKILL.md" if files.empty?

    # The registered-skill shape: every immediate child directory of a root is a
    # skill directory and must hold a SKILL.md. This catches a skill directory
    # that lost its file, which the recursive walk alone would report only as a
    # smaller corpus.
    list_directory(dir).sort.each do |entry|
      path = File.join(dir, entry)
      next unless File.directory?(path)

      unless File.file?(File.join(path, "SKILL.md"))
        raise TraversalError, "skill directory #{label}/#{entry} has no SKILL.md"
      end
    end

    corpus[label] = files
  end

  # The internal root needs no check here: skill_roots builds it through
  # require_own_directory, which already raises when it is missing or not a
  # directory. A second check would be unreachable, and deleting it turns no
  # green red -- so it does not exist.
  if corpus.keys.none? { |label| label.start_with?("plugins/") }
    raise TraversalError, "no plugins/*/skills root was found"
  end

  corpus
end

# NOT fixture-pinned, and deliberately so: there is no uid-independent way to
# make a file unreadable here (chmod 000 is a no-op as root, a directory named
# SKILL.md never reaches this call, and a broken symlink is refused earlier by
# the child guard). Deleting the rescue is not a false GREEN either -- an
# unreadable file still fails, just with a misleading "no frontmatter block"
# instead of the errno. The rescue exists for criterion 2's "name the
# construct", and this comment exists so the mutation-coverage claim elsewhere
# is not read as covering it.
def read_skill(path)
  File.read(path, mode: "rb").force_encoding(Encoding::UTF_8)
rescue SystemCallError => e
  raise TraversalError, "cannot read #{path} (#{e.class})"
end

# ---------------------------------------------------------------------------
# Self-tests
#
# These run BEFORE the real corpus and the gate refuses to report on the corpus
# if any of them fails. Every expected length is known BY CONSTRUCTION ("A" *
# 1025), never by a second measurement of the same input -- the previous
# attempt's differential shared its extractor with the code under test and so
# corroborated nothing.
# ---------------------------------------------------------------------------

class SelfTests
  attr_reader :failures, :count

  def initialize
    @failures = []
    @count = 0
  end

  # Expects the file to pass cleanly, and (optionally) to measure exactly the
  # constructed lengths.
  def expect_pass(label, source, lengths: nil)
    @count += 1
    problems, measured = measure_frontmatter(source)
    if !problems.empty?
      @failures << "#{label}: expected a clean pass, got #{problems.inspect}"
    elsif lengths && !lengths.all? { |field, n| measured[field] == n }
      @failures << "#{label}: expected lengths #{lengths.inspect}, measured #{measured.inspect}"
    end
  rescue FrontmatterError => e
    @failures << "#{label}: expected a clean pass, got FrontmatterError: #{e.message}"
  end

  # Expects a problem (or a FrontmatterError) whose text contains every needle.
  # The needles are what make a row pin its REASON: a fixture that goes red for
  # the wrong reason passes a bare "expect red" assertion unchanged.
  def expect_red(label, source, *needles)
    @count += 1
    reported =
      begin
        problems, = measure_frontmatter(source)
        problems
      rescue FrontmatterError => e
        [e.message]
      end

    if reported.empty?
      @failures << "#{label}: expected a violation, got a clean pass"
      return
    end
    record_missing(label, needles, reported.join(" | "), "the report")
  end

  # Expects a TraversalError from a block, naming the construct.
  def expect_traversal_error(label, *needles)
    @count += 1
    yield
    @failures << "#{label}: expected a TraversalError, none raised"
  rescue TraversalError => e
    record_missing(label, needles, e.message, "the error")
  end

  # Runs this file as a child process against a fixture repo root and requires a
  # non-zero exit whose output contains every needle.
  def expect_process_red(label, repo_root, *needles)
    @count += 1
    output = IO.popen(
      { "SKILL_FRONTMATTER_CHILD" => "1" },
      [RbConfig.ruby, __FILE__, repo_root, { err: [:child, :out] }]
    ) { |io| io.read }

    if $?.success?
      @failures << "#{label}: expected a non-zero exit, got 0 with #{output.inspect}"
      return
    end
    record_missing(label, needles, output, "the child output")
  end

  # The needle list is what makes a fixture pin its REASON rather than mere
  # redness, so every call site keeps its own needles; only the checking is
  # shared.
  def record_missing(label, needles, text, context)
    missing = needles.reject { |needle| text.include?(needle) }
    return if missing.empty?

    @failures << "#{label}: expected #{missing.inspect} in #{context}, got #{text.inspect}"
  end

  def expect_traversal_ok(label)
    @count += 1
    yield
  rescue TraversalError => e
    @failures << "#{label}: unexpected TraversalError: #{e.message}"
  end
end

# mkdir_p + write for one fixture SKILL.md. The RELATIVE PATH stays a literal at
# every call site, so the exact filesystem shape each fixture depends on is still
# readable where it is used. This collapses two mechanical lines; it is not a
# fixture DSL, and the bare mkdir_p calls that create a directory WITHOUT a
# SKILL.md are left alone -- those absences are the point of their fixtures.
def write_skill(root, relative_path, body)
  path = File.join(root, relative_path)
  FileUtils.mkdir_p(File.dirname(path))
  File.write(path, body)
end

def frontmatter_file(body)
  "---\n#{body}\n---\nBody text.\n"
end

def run_self_tests
  t = SelfTests.new
  # Written as an ESCAPE, never as the literal character. This fixture is the
  # only thing that kills a bytesize-for-length mutant, and it only does so
  # while the character is a real NBSP: 100 NBSP + 1000 "A" is 1100 characters
  # but 1200 bytes, whereas 100 plain spaces are 1100 of both. Normalise that
  # one character -- which any editor or copy path may silently do -- and the
  # fixture keeps passing while quietly measuring nothing. Same reason for the
  # \uFEFF escapes above and below.
  nbsp = "\u00A0"

  # --- the caps themselves, at and over the boundary ------------------------
  t.expect_pass("description of exactly 1024",
                frontmatter_file("name: a\ndescription: #{'A' * 1024}"),
                lengths: { "name" => 1, "description" => 1024 })
  t.expect_red("description of exactly 1025",
               frontmatter_file("name: a\ndescription: #{'A' * 1025}"),
               "description", "1025", "exceeds the maximum of 1024")
  t.expect_pass("name of exactly 64",
                frontmatter_file("name: #{'n' * 64}\ndescription: d"),
                lengths: { "name" => 64 })
  t.expect_red("name of exactly 65",
               frontmatter_file("name: #{'n' * 65}\ndescription: d"),
               "name", "65", "exceeds the maximum of 64")
  t.expect_pass("compatibility of exactly 500",
                frontmatter_file("name: a\ndescription: d\ncompatibility: #{'c' * 500}"),
                lengths: { "compatibility" => 500 })
  t.expect_red("compatibility of exactly 501",
               frontmatter_file("name: a\ndescription: d\ncompatibility: #{'c' * 501}"),
               "compatibility", "501", "exceeds the maximum of 500")

  # --- lower bounds ---------------------------------------------------------
  # Written as explicit empty STRINGS. A bare `name:` parses to nil and would go
  # red through the type check instead, so it would still be red with the lower
  # bound deleted -- the needle pins which rule fired.
  t.expect_red("empty name", frontmatter_file(%(name: ""\ndescription: d)),
               "name", "length 0 is below the minimum of 1")
  t.expect_red("empty description", frontmatter_file(%(name: a\ndescription: "")),
               "description", "length 0 is below the minimum of 1")
  t.expect_red("empty compatibility",
               frontmatter_file(%(name: a\ndescription: d\ncompatibility: "")),
               "compatibility", "length 0 is below the minimum of 1")

  # --- nil and wrong types --------------------------------------------------
  t.expect_red("bare name (nil)", frontmatter_file("name:\ndescription: d"),
               "name", "NilClass")
  t.expect_red("bare description (nil)", frontmatter_file("name: a\ndescription:"),
               "description", "NilClass")
  t.expect_red("numeric description", frontmatter_file("name: a\ndescription: 42"),
               "description", "Integer", "not a String")

  # --- required fields ------------------------------------------------------
  t.expect_red("missing name", frontmatter_file("description: d"),
               "name", "required field is absent")
  t.expect_red("missing description", frontmatter_file("name: a"),
               "description", "required field is absent")

  # --- the seven constructs the hand-rolled reader under-measured -----------
  # Each expected length was measured against Psych and matches the value the
  # rejected draft's review recorded.
  t.expect_red("alias to an anchored 1100-char scalar",
               frontmatter_file("name: a\nanchor: &d #{'B' * 1100}\ndescription: *d"),
               "description", "1100")
  t.expect_red("block scalar with an internal blank line",
               frontmatter_file("name: a\ndescription: |-\n  #{'B' * 600}\n\n  #{'B' * 600}"),
               "description", "1202")
  # No trailing body: the `|` block ends immediately before the closing `---`,
  # which is the case that measures 1025 with the newline inside the capture and
  # 1024 without it.
  t.expect_red("block scalar ending at the closing delimiter",
               "---\nname: a\ndescription: |\n  #{'A' * 1024}\n---\nBody text.\n",
               "description", "1025")
  t.expect_red("keep-chomped block with trailing blank lines",
               "---\nname: a\ndescription: |+\n  #{'A' * 1023}\n\n\n\n\n---\nBody text.\n",
               "description", "1028")
  t.expect_red("non-breaking spaces before the text",
               frontmatter_file("name: a\ndescription: #{nbsp * 100}#{'A' * 1000}"),
               "description", "1100")
  t.expect_red("duplicate description key, last one wins",
               frontmatter_file("name: a\ndescription: ab\ndescription: #{'C' * 1100}"),
               "description", "1100")
  t.expect_red("description at column zero inside a flow mapping",
               frontmatter_file("name: a\nflow: {q: 1,\ndescription: xy}\ndescription: #{'D' * 1100}"),
               "description", "1100")

  # --- shape of the document ------------------------------------------------
  t.expect_red("scalar-only frontmatter", "---\nname description\n---\nBody text.\n",
               "String", "not a mapping")
  t.expect_red("sequence frontmatter", "---\n- name: a\n---\nBody text.\n",
               "Array", "not a mapping")
  t.expect_red("empty frontmatter", "---\n\n---\nBody text.\n",
               "NilClass", "not a mapping")
  t.expect_red("malformed YAML", "---\nname: [a\ndescription: d\n---\nBody text.\n",
               "Psych::SyntaxError")

  # --- framing --------------------------------------------------------------
  t.expect_pass("CRLF frontmatter",
                "---\r\nname: a\r\ndescription: hello\r\n---\r\nBody text.\r\n",
                lengths: { "description" => 5 })
  t.expect_pass("no body after the closing delimiter",
                "---\nname: a\ndescription: hello\n---\n",
                lengths: { "description" => 5 })
  t.expect_red("UTF-8 BOM", "\uFEFF---\nname: a\ndescription: d\n---\nBody.\n", "BOM")
  t.expect_red("leading blank line", "\n---\nname: a\ndescription: d\n---\nBody.\n", "blank line")
  t.expect_red("closed by ...", "---\nname: a\ndescription: d\n...\nBody.\n", "'...'")
  t.expect_red("no frontmatter at all", "# Just a heading\n", "no '---' frontmatter block")

  # --- traversal ------------------------------------------------------------
  Dir.mktmpdir("skill-frontmatter-selftest") do |tmp|
    valid = frontmatter_file("name: ok\ndescription: fine")

    # A well-formed miniature repo: one internal skill, one plugin skill nested
    # two levels deep, and one plugin that ships no skills at all.
    good = File.join(tmp, "good")
    write_skill(good, ".claude/skills/alpha/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(good, "plugins", "p1", "skills", "outer", "nested"))
    write_skill(good, "plugins/p1/skills/outer/SKILL.md", valid)
    write_skill(good, "plugins/p1/skills/outer/nested/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(good, "plugins", "p2", "commands"))

    t.expect_traversal_ok("well-formed fixture repo") do
      corpus = collect_corpus(good)
      unless corpus[".claude/skills"].length == 1
        raise TraversalError, "expected 1 internal skill, got #{corpus['.claude/skills'].length}"
      end
      # The nested file is the pin for the recursive walk: a fixed-depth walk
      # finds one file here and leaves every count self-consistent.
      unless corpus["plugins/p1/skills"].length == 2
        raise TraversalError, "expected 2 plugin skills (one nested), got #{corpus['plugins/p1/skills'].length}"
      end
      if corpus.key?("plugins/p2/skills")
        raise TraversalError, "a plugin with no skills/ directory must not appear as a root"
      end
    end

    # A skill directory that lost its SKILL.md, BESIDE a valid sibling. The
    # sibling is load-bearing: without it the root holds zero skills and the
    # per-root floor fires instead, so this fixture would stay red even if the
    # missing-file check were deleted entirely.
    orphan = File.join(tmp, "orphan")
    write_skill(orphan, ".claude/skills/alpha/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(orphan, ".claude", "skills", "beta"))
    write_skill(orphan, "plugins/p1/skills/one/SKILL.md", valid)
    t.expect_traversal_error("skill directory with no SKILL.md", "beta", "has no SKILL.md") do
      collect_corpus(orphan)
    end

    # An empty plugins/*/skills root: the per-root floor.
    empty = File.join(tmp, "empty")
    write_skill(empty, ".claude/skills/alpha/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(empty, "plugins", "p1", "skills"))
    t.expect_traversal_error("empty plugins skills root", "contains no SKILL.md") do
      collect_corpus(empty)
    end

    # No plugins/*/skills root at all.
    noplugins = File.join(tmp, "noplugins")
    write_skill(noplugins, ".claude/skills/alpha/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(noplugins, "plugins", "p1"))
    t.expect_traversal_error("no plugins skills root", "no plugins/*/skills root") do
      collect_corpus(noplugins)
    end

    # Traversal failures must surface, never shrink the corpus. Two distinct
    # SystemCallError subclasses go through the same Dir.children call, and both
    # are uid-independent -- unlike a chmod-000 probe, which is a no-op for root.
    t.expect_traversal_error("missing required parent", "cannot stat", "ENOENT") do
      collect_corpus(File.join(tmp, "does-not-exist"))
    end

    notdir = File.join(tmp, "notdir")
    FileUtils.mkdir_p(notdir)
    File.write(File.join(notdir, "plugins"), "I am a file, not a directory.\n")
    write_skill(notdir, ".claude/skills/alpha/SKILL.md", valid)
    t.expect_traversal_error("required parent is a file", "cannot list", "ENOTDIR") do
      collect_corpus(notdir)
    end

    # The internal root exists but is a regular file: require_own_directory's
    # other branch. The notdir fixture above makes `plugins` a file, not this
    # one, so without this row that branch is reachable but unpinned.
    rootfile = File.join(tmp, "rootfile")
    FileUtils.mkdir_p(File.join(rootfile, ".claude"))
    File.write(File.join(rootfile, ".claude", "skills"), "not a directory\n")
    write_skill(rootfile, "plugins/p1/skills/one/SKILL.md", valid)
    t.expect_traversal_error("internal root is a regular file",
                             ".claude/skills", "is not a directory") do
      collect_corpus(rootfile)
    end

    # A symlink at an ANCESTOR of a root, not at the root itself. lstat only
    # declines to follow the final component, so without a per-component walk
    # these two are followed and the gate measures a redirected tree while
    # labelling it `.claude/skills` and `plugins/p1/skills`.
    claude_link = File.join(tmp, "claude-ancestor-link")
    FileUtils.mkdir_p(claude_link)
    write_skill(claude_link, "elsewhere/skills/alpha/SKILL.md", valid)
    File.symlink(File.join(claude_link, "elsewhere"), File.join(claude_link, ".claude"))
    write_skill(claude_link, "plugins/p1/skills/one/SKILL.md", valid)
    t.expect_traversal_error("the .claude ancestor is a symlink", ".claude", "symlink") do
      collect_corpus(claude_link)
    end

    plugins_link = File.join(tmp, "plugins-ancestor-link")
    write_skill(plugins_link, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(plugins_link, "elsewhere/p1/skills/one/SKILL.md", valid)
    File.symlink(File.join(plugins_link, "elsewhere"), File.join(plugins_link, "plugins"))
    t.expect_traversal_error("the plugins root is a symlink", "plugins", "symlink") do
      collect_corpus(plugins_link)
    end

    # Symlinked ROOTS, not just symlinked children. A followed root would measure
    # another tree's files under this root's label -- the wrong corpus, not a
    # smaller one -- so both roots are lstat-ed before any walk.
    symlinked = File.join(tmp, "symlinked")
    write_skill(symlinked, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(symlinked, "plugins/real/skills/one/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(symlinked, "plugins", "borrower"))
    File.symlink(File.join(symlinked, "plugins", "real", "skills"),
                 File.join(symlinked, "plugins", "borrower", "skills"))
    t.expect_traversal_error("plugin skills root is a symlink", "borrower", "symlink") do
      collect_corpus(symlinked)
    end

    internal_link = File.join(tmp, "internal-link")
    write_skill(internal_link, "elsewhere/alpha/SKILL.md", valid)
    FileUtils.mkdir_p(File.join(internal_link, ".claude"))
    File.symlink(File.join(internal_link, "elsewhere"), File.join(internal_link, ".claude", "skills"))
    write_skill(internal_link, "plugins/p1/skills/one/SKILL.md", valid)
    t.expect_traversal_error("internal skills root is a symlink", ".claude/skills", "symlink") do
      collect_corpus(internal_link)
    end

    # A symlinked directory INSIDE a skills tree. Without the child-level
    # refusal this is not an error but a SILENT SKIP: lstat reports a symlink,
    # `stat.directory?` is false, the entry is not named SKILL.md, and the
    # subtree simply never appears in the corpus -- a smaller corpus, which is
    # the one outcome acceptance criterion 4 forbids.
    nested_link = File.join(tmp, "nested-link")
    write_skill(nested_link, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(nested_link, "plugins/p1/skills/one/SKILL.md", valid)
    write_skill(nested_link, "outside/two/SKILL.md", valid)
    # Deliberately NOT an immediate child of the root: at that level the
    # orphan-skill-directory check would fire first and this fixture would go
    # red without the symlink refusal existing at all.
    File.symlink(File.join(nested_link, "outside"),
                 File.join(nested_link, "plugins", "p1", "skills", "one", "linked"))
    t.expect_traversal_error("symlinked directory inside a skills tree", "linked", "symlink") do
      collect_corpus(nested_link)
    end

    # A symlink directly AT plugins/<entry>. This is the fourth refusal site and
    # the one round 2 caught unpinned: deleting it does not raise, it falls
    # through `next unless plugin_stat.directory?` and skips the entry in
    # silence.
    plugin_link = File.join(tmp, "plugin-link")
    write_skill(plugin_link, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(plugin_link, "plugins/real/skills/one/SKILL.md", valid)
    File.symlink(File.join(plugin_link, "plugins", "real"),
                 File.join(plugin_link, "plugins", "clone"))
    t.expect_traversal_error("plugin entry itself is a symlink", "clone", "symlink") do
      collect_corpus(plugin_link)
    end

    # An ordinary file directly under plugins/ is not a plugin and must not turn
    # an unrelated skill edit red by probing "plugins/README.md/skills".
    stray = File.join(tmp, "stray")
    write_skill(stray, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(stray, "plugins/p1/skills/one/SKILL.md", valid)
    File.write(File.join(stray, "plugins", "README.md"), "# Plugins\n")
    t.expect_traversal_ok("ordinary file directly under plugins/") do
      corpus = collect_corpus(stray)
      unless corpus.length == 2
        raise TraversalError, "expected 2 roots, got #{corpus.keys.inspect}"
      end

    # THE ENTRY POINT, not a helper: re-invoke this very file against a fixture
    # repo holding one over-limit SKILL.md and require that the PROCESS exits
    # non-zero naming that file. Acceptance criterion 6 asks for RED on a known
    # violation "for the right reason, naming the right file", and nothing else
    # here exercises main's reporting and exit path.
    violating = File.join(tmp, "violating")
    write_skill(violating, ".claude/skills/alpha/SKILL.md", valid)
    write_skill(violating, "plugins/p1/skills/toolong/SKILL.md",
                frontmatter_file("name: toolong\ndescription: #{'A' * 1025}"))
    t.expect_process_red("over-limit file reported by the entry point", violating,
                         "plugins/p1/skills/toolong/SKILL.md", "1025",
                         "exceeds the maximum of 1024")
    end
  end

  t
end

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Scan one repo root. Returns [violations, per-root counts]; violations are
# "<relative path>: <problem>" strings. Extracted from main so the REPORTING
# path -- not just measure_frontmatter -- is reachable from a fixture. Without
# that, deleting main's non-zero exit leaves every self-test green while the
# gate stops failing on a real violation.
def scan_corpus(repo_root)
  corpus = collect_corpus(repo_root)
  violations = []
  counts = {}

  corpus.each do |label, files|
    counts[label] = files.length
    files.each do |path|
      relative = path.sub("#{repo_root}/", "")
      begin
        problems, = measure_frontmatter(read_skill(path))
      rescue FrontmatterError => e
        violations << "#{relative}: #{e.message}"
        next
      end
      problems.each { |problem| violations << "#{relative}: #{problem}" }
    end
  end

  [violations, counts]
end

def main(repo_root)
  puts "ruby #{RUBY_VERSION}, psych #{Psych::VERSION}"

  begin
    violations, counts = scan_corpus(repo_root)
  rescue TraversalError => e
    warn "CORPUS ERROR: #{e.message}"
    exit 1
  end

  # Per root, not in total: a walk that iterates zero times otherwise prints
  # exactly what a passing one prints.
  counts.each { |label, n| puts "  #{label}: #{n} SKILL.md" }
  puts "measured #{counts.values.sum} SKILL.md across #{counts.length} roots"

  unless violations.empty?
    warn "\nFRONTMATTER LIMIT VIOLATIONS (#{violations.length}):"
    violations.each { |v| warn "  - #{v}" }
    exit 1
  end

  puts "OK: every SKILL.md frontmatter is within the Agent Skills length caps."
end

if $PROGRAM_NAME == __FILE__
  # A child process re-invokes this file to pin the ENTRY POINT: that a real
  # violation makes the PROCESS exit non-zero and names the offending file.
  # Testing scan_corpus alone would leave main's exit unpinned.
  if ENV["SKILL_FRONTMATTER_CHILD"]
    main(ARGV.fetch(0))
  else
    tests = run_self_tests
    unless tests.failures.empty?
      warn "SELF-TESTS FAILED (#{tests.failures.length} of #{tests.count}):"
      tests.failures.each { |f| warn "  - #{f}" }
      warn "\nRefusing to report on the real corpus: the measurement is not trustworthy."
      exit 1
    end
    # The self-test suite is itself subject to the zero-iteration failure it
    # exists to catch: a suite that runs no cases reports no failures and reads
    # exactly like a passing one. No expected TOTAL is pinned here -- that would
    # freeze the fixture table -- only that the suite ran at all.
    unless tests.count.positive?
      warn "SELF-TESTS DID NOT RUN: 0 cases executed."
      exit 1
    end
    puts "self-tests: #{tests.count} passed"
    main(File.expand_path("..", __dir__))
  end
end
