"""Tests for hr.policy — command policy validator."""

import pytest

from hr.policy import PolicyViolation, validate


# ============================================================
# Allowed commands (from requirements.md §7.2)
# ============================================================

class TestAllowed:
    def test_simple_command(self):
        validate("gh repo list --limit 10")

    def test_single_pipe(self):
        validate("git log --oneline | head -20")

    def test_multi_pipe(self):
        validate("cat some.json | jq '.' | grep \"some_val\"")

    def test_multi_pipe_sort(self):
        validate("ls -la ~/Projects | sort -k5 -rn | head -10")

    def test_env_prefix(self):
        validate("GH_TOKEN=abc123 gh api /user")

    def test_docker_format(self):
        validate("docker ps --format '{{.Names}}' | grep web")

    def test_python_inline(self):
        validate("python3 -c \"import sys; print(sys.version)\"")

    def test_cat_home_file(self):
        validate("cat ~/.ssh/id_rsa.pub")

    def test_simple_echo(self):
        validate("echo hello world")

    def test_ls(self):
        validate("ls -la")

    def test_redirect_to_home(self):
        validate("echo hello > ~/out.txt")

    def test_redirect_to_tmp(self):
        validate("echo hello > /tmp/out.txt")

    def test_redirect_to_devnull(self):
        validate("echo hello > /dev/null")

    def test_stderr_redirect(self):
        validate("cmd 2> /dev/null")

    def test_fd_dup(self):
        validate("cmd 2>&1")

    def test_relative_redirect(self):
        validate("echo hello > output.txt")

    def test_python_c_flag(self):
        validate("python3 -c \"print(1)\"")

    def test_python_m_flag(self):
        validate("python3 -m http.server")

    def test_four_stage_pipe(self):
        validate("cat file | grep pattern | sort | uniq -c")

    def test_env_assign_multiple(self):
        validate("FOO=1 BAR=2 some_command arg1 arg2")


# ============================================================
# Rejected commands (from requirements.md §7.3 + additional)
# ============================================================

class TestRejected:
    def test_semicolon(self):
        with pytest.raises(PolicyViolation, match="no_statement_separators"):
            validate("gh repo list; rm -rf ~")

    def test_and_and(self):
        with pytest.raises(PolicyViolation, match="no_statement_separators"):
            validate("test -f x && echo yes")

    def test_or_or(self):
        with pytest.raises(PolicyViolation, match="no_statement_separators"):
            validate("test -f x || echo no")

    def test_command_substitution_dollar(self):
        with pytest.raises(PolicyViolation, match="no_command_substitution"):
            validate("$(whoami)")

    def test_command_substitution_backtick(self):
        with pytest.raises(PolicyViolation, match="no_command_substitution"):
            validate("echo `whoami`")

    def test_bash_as_command(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("bash -c \"rm -rf /\"")

    def test_sh_as_command(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("sh -c \"echo hacked\"")

    def test_python_script_file(self):
        with pytest.raises(PolicyViolation, match="no_interpreter_script"):
            validate("python3 /home/user/evil.py")

    def test_python_script_relative(self):
        with pytest.raises(PolicyViolation, match="no_interpreter_script"):
            validate("python3 script.py")

    def test_nohup(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("nohup long-job")

    def test_background_ampersand(self):
        with pytest.raises(PolicyViolation, match="no_background"):
            validate("sleep 100 &")

    def test_bash_in_pipe(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("cmd1 | cmd2 | bash")

    def test_redirect_outside_home(self):
        with pytest.raises(PolicyViolation, match="redirect_outside_home"):
            validate("cat /etc/passwd | tee ~/out.txt > /etc/hosts")

    def test_newline(self):
        with pytest.raises(PolicyViolation, match="no_control_chars"):
            validate("echo hello\necho world")

    def test_process_substitution_input(self):
        with pytest.raises(PolicyViolation, match="no_process_substitution"):
            validate("diff <(cmd1) <(cmd2)")

    def test_process_substitution_output(self):
        with pytest.raises(PolicyViolation, match="no_process_substitution"):
            validate("cmd >(tee log)")

    def test_subshell(self):
        with pytest.raises(PolicyViolation, match="no_subshell"):
            validate("(echo inside subshell)")

    def test_eval(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("eval 'dangerous command'")

    def test_exec(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("exec /bin/bad")

    def test_source(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("source ~/.bashrc")

    def test_dot_source(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate(". ~/.bashrc")

    def test_setsid(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("setsid some-daemon")

    def test_screen(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("screen -d -m long-process")

    def test_tmux(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("tmux new-session -d 'cmd'")

    def test_disown(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("disown")

    def test_empty_command(self):
        with pytest.raises(PolicyViolation, match="empty_command"):
            validate("")

    def test_only_whitespace(self):
        with pytest.raises(PolicyViolation, match="empty_command"):
            validate("   ")

    def test_node_script(self):
        with pytest.raises(PolicyViolation, match="no_interpreter_script"):
            validate("node /path/to/evil.js")

    def test_perl_script(self):
        with pytest.raises(PolicyViolation, match="no_interpreter_script"):
            validate("perl script.pl")

    def test_ruby_script(self):
        with pytest.raises(PolicyViolation, match="no_interpreter_script"):
            validate("ruby exploit.rb")

    def test_redirect_to_etc(self):
        with pytest.raises(PolicyViolation, match="redirect_outside_home"):
            validate("echo hack > /etc/passwd")

    def test_redirect_input_outside_home(self):
        with pytest.raises(PolicyViolation, match="redirect_outside_home"):
            validate("cmd < /etc/shadow")

    def test_daemon_command(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("daemon some-service")

    def test_start_stop_daemon(self):
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("start-stop-daemon --start --exec /usr/sbin/sshd")


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:
    def test_quoted_semicolon(self):
        """Semicolons inside quotes are literal text."""
        validate("echo \"a;b\"")

    def test_single_quoted_semicolon(self):
        validate("echo 'a;b'")

    def test_quoted_backtick(self):
        """Backticks inside single quotes are literal."""
        validate("echo 'use `backtick`'")

    def test_awk_braces(self):
        """awk is blocked because it has system() built-in."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("awk '{print $1}'")

    def test_docker_go_template(self):
        """Docker Go template braces in quotes."""
        validate("docker inspect --format '{{.State.Running}}'")

    def test_grep_with_quotes(self):
        validate("grep 'pattern with spaces' file.txt")

    def test_escaped_semicolon(self):
        """Escaped semicolons are literal."""
        validate("echo hello\\; world")

    def test_fd_duplication_2_to_1(self):
        """2>&1 is a valid fd duplication, not background."""
        validate("cmd 2>&1")

    def test_ampersand_redirect(self):
        """&> is a valid redirect, not background."""
        validate("cmd &>/dev/null")

    def test_pipe_with_spaces(self):
        validate("cat file.txt  |  grep pattern  |  wc -l")

    def test_node_e_flag(self):
        """node -e is an inline expression, should be allowed."""
        validate("node -e \"console.log(1)\"")

    def test_perl_e_flag(self):
        validate("perl -e 'print 1'")

    def test_unclosed_single_quote(self):
        with pytest.raises(PolicyViolation, match="unclosed_quote"):
            validate("echo 'hello")

    def test_unclosed_double_quote(self):
        with pytest.raises(PolicyViolation, match="unclosed_quote"):
            validate('echo "hello')

    def test_command_sub_in_double_quotes(self):
        """$() inside double quotes is still command substitution."""
        with pytest.raises(PolicyViolation, match="no_command_substitution"):
            validate('echo "$(whoami)"')

    def test_backtick_in_double_quotes(self):
        with pytest.raises(PolicyViolation, match="no_command_substitution"):
            validate('echo "`whoami`"')

    def test_env_only_no_command(self):
        """VAR=val without a command should fail."""
        with pytest.raises(PolicyViolation, match="no_executable"):
            validate("FOO=bar")

    def test_extra_blocked(self):
        """Extra blocked executables from config."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("custom_bad_cmd arg", extra_blocked=["custom_bad_cmd"])

    def test_nohup_in_pipe_stage(self):
        """nohup appearing in any pipe stage should be caught."""
        with pytest.raises(PolicyViolation):
            validate("echo test | nohup bad_cmd")

    def test_disown_in_middle(self):
        """disown token in any stage arguments."""
        with pytest.raises(PolicyViolation):
            validate("echo test | cmd disown")


# ============================================================
# Security hardening tests (from audit)
# ============================================================

class TestSecurityHardening:
    """Tests for vulnerabilities found in security audit."""

    def test_ansi_c_quoting_blocked(self):
        """$'...' can encode blocked executable names — must be rejected."""
        with pytest.raises(PolicyViolation, match="no_ansi_c_quoting"):
            validate("$'\\x62\\x61\\x73\\x68' -c 'evil'")

    def test_dollar_var_blocked(self):
        """$VAR can expand to arbitrary command — must be rejected."""
        with pytest.raises(PolicyViolation, match="no_variable_expansion"):
            validate("$EVIL_CMD arg1")

    def test_dollar_brace_var_blocked(self):
        """${VAR} variable expansion — must be rejected."""
        with pytest.raises(PolicyViolation, match="no_variable_expansion"):
            validate("${EVIL_CMD} arg1")

    def test_env_wrapper_blocked(self):
        """env can invoke blocked executables — must be rejected."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("env bash -c 'evil'")

    def test_sudo_blocked(self):
        """sudo must be rejected."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("sudo rm -rf /")

    def test_nice_blocked(self):
        """nice can wrap blocked commands — must be rejected."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("nice bash -c 'evil'")

    def test_awk_blocked(self):
        """awk has system() built-in — must be rejected."""
        with pytest.raises(PolicyViolation, match="blocked_executable"):
            validate("awk 'BEGIN{system(\"bash\")}'")

    def test_null_byte_rejected(self):
        """Null bytes in commands must be rejected."""
        with pytest.raises(PolicyViolation, match="no_null_bytes"):
            validate("echo\x00evil")

    def test_control_char_rejected(self):
        """Control characters (except tab) must be rejected."""
        with pytest.raises(PolicyViolation, match="no_control_chars"):
            validate("echo \x01 evil")

    def test_redirect_traversal_tilde(self):
        """~/../../etc/passwd traversal must be caught."""
        with pytest.raises(PolicyViolation, match="redirect_outside_home"):
            validate("echo x > ~/../../etc/passwd")

    def test_dollar_in_single_quotes_ok(self):
        """$VAR inside single quotes is literal — should be allowed."""
        validate("echo '$HOME is safe'")

    def test_dollar_in_double_quotes_expansion_blocked(self):
        """$VAR inside double quotes still expands — must be rejected."""
        with pytest.raises(PolicyViolation, match="no_variable_expansion"):
            validate('echo "$HOME"')

    def test_tab_in_command_ok(self):
        """Tabs are valid whitespace, not control characters."""
        validate("echo\thello")
