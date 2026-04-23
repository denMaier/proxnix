from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from proxnix_workstation.config import WorkstationConfig, load_workstation_config
from proxnix_workstation.exercise_cli import build_generated_config, render_config_file
from proxnix_workstation.keepass_agent import (
    available_pykeepass_agent_public_keys,
    derive_pykeepass_agent_password,
)
from proxnix_workstation.publish_cli import build_compiled_secret_store
from proxnix_workstation.secrets_cli import cmd_print_keepass_password
from proxnix_workstation.secret_provider import ExecSecretProvider, NamedSecretProvider, load_secret_provider
from proxnix_workstation.paths import SitePaths
from proxnix_workstation.errors import ProxnixWorkstationError
from proxnix_workstation.secret_provider_adapters import (
    BitwardenSecretsAdapter,
    GoPassAdapter,
    InfisicalAdapter,
    KeePassXCCliAdapter,
    OnePasswordAdapter,
    PassAdapter,
    PassholeAdapter,
    PyKeePassAdapter,
    VaultKvAdapter,
)


def _test_config(*, site_dir: Path | None = None) -> WorkstationConfig:
    return WorkstationConfig(
        config_file=Path("/tmp/proxnix-config"),
        site_dir=site_dir,
        hosts=("root@node1",),
        ssh_identity=None,
        remote_dir=PurePosixPath("/var/lib/proxnix"),
        remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
        remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
        secret_provider="embedded-sops",
        secret_provider_command=None,
        provider_environment=(("PROXNIX_SOPS_MASTER_IDENTITY", "/tmp/id_master"),),
    )


class _FakeProvider:
    def __init__(self, scopes: dict[tuple[str, str | None], dict[str, str]]) -> None:
        self.scopes = scopes

    def export_scope(self, ref) -> dict[str, str]:
        key = (ref.scope, ref.group or ref.vmid)
        return dict(self.scopes.get(key, {}))

    def has_any(self, ref) -> bool:
        return bool(self.export_scope(ref))


class ExecSecretProviderTests(unittest.TestCase):
    def test_exec_provider_falls_back_to_list_plus_get_when_export_scope_missing(self) -> None:
        provider = ExecSecretProvider(["provider-helper"])
        responses = [
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"capabilities":["list","get"]}\n', stderr=""),
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"names":["alpha","beta"]}\n', stderr=""),
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"value":"one"}\n', stderr=""),
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"value":"two"}\n', stderr=""),
        ]

        with patch("proxnix_workstation.secret_provider.run_command", side_effect=responses) as run_mock:
            data = provider.export_scope(type("Ref", (), {"scope": "shared", "vmid": None, "group": None, "cli_args": lambda self: ["--scope", "shared"]})())

        self.assertEqual(data, {"alpha": "one", "beta": "two"})
        self.assertEqual(run_mock.call_args_list[1].args[0], ["provider-helper", "list", "--scope", "shared"])

    def test_exec_provider_rejects_invalid_json_payload(self) -> None:
        provider = ExecSecretProvider(["provider-helper"])
        responses = [
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"capabilities":["export-scope"]}\n', stderr=""),
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"data":["bad"]}\n', stderr=""),
        ]

        with patch("proxnix_workstation.secret_provider.run_command", side_effect=responses):
            with self.assertRaises(ProxnixWorkstationError):
                provider.export_scope(type("Ref", (), {"scope": "shared", "vmid": None, "group": None, "cli_args": lambda self: ["--scope", "shared"]})())

    def test_exec_provider_forwards_configured_provider_environment(self) -> None:
        provider = ExecSecretProvider(
            ["provider-helper"],
            extra_env={
                "PROXNIX_PASS_STORE_DIR": "/tmp/pass-store",
                "VAULT_ADDR": "https://vault.example.test",
            },
        )
        responses = [
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"capabilities":["export-scope"]}\n', stderr=""),
            CompletedProcess(args=["provider-helper"], returncode=0, stdout='{"data":{}}\n', stderr=""),
        ]

        with patch("proxnix_workstation.secret_provider.run_command", side_effect=responses) as run_mock:
            provider.export_scope(type("Ref", (), {"scope": "shared", "vmid": None, "group": None, "cli_args": lambda self: ["--scope", "shared"]})())

        forwarded_env = run_mock.call_args_list[1].kwargs["env"]
        self.assertEqual(forwarded_env["PROXNIX_PASS_STORE_DIR"], "/tmp/pass-store")
        self.assertEqual(forwarded_env["VAULT_ADDR"], "https://vault.example.test")

    def test_named_provider_alias_returns_internal_named_adapter(self) -> None:
        config = _test_config()
        config = WorkstationConfig(
            config_file=config.config_file,
            site_dir=config.site_dir,
            hosts=config.hosts,
            ssh_identity=config.ssh_identity,
            remote_dir=config.remote_dir,
            remote_priv_dir=config.remote_priv_dir,
            remote_host_relay_identity=config.remote_host_relay_identity,
            secret_provider="pass",
            secret_provider_command=None,
            provider_environment=config.provider_environment,
        )

        provider = load_secret_provider(config)

        self.assertIsInstance(provider, NamedSecretProvider)
        self.assertEqual(provider.describe(), "pass")

    def test_named_provider_aliases_include_passhole_pykeepass_bws_vault_op_and_infisical(self) -> None:
        base = _test_config()
        expected_names = {
            "passhole": "passhole",
            "pykeepass": "pykeepass",
            "bws": "bws",
            "bitwarden-secrets": "bws",
            "vault": "vault-kv",
            "vault-kv": "vault-kv",
            "op": "op",
            "1password": "op",
            "onepassword": "op",
            "infisical": "infisical",
        }
        for alias in (
            "passhole",
            "pykeepass",
            "bws",
            "bitwarden-secrets",
            "vault",
            "vault-kv",
            "op",
            "1password",
            "onepassword",
            "infisical",
        ):
            config = WorkstationConfig(
                config_file=base.config_file,
                site_dir=base.site_dir,
                hosts=base.hosts,
                ssh_identity=base.ssh_identity,
                remote_dir=base.remote_dir,
                remote_priv_dir=base.remote_priv_dir,
                remote_host_relay_identity=base.remote_host_relay_identity,
                secret_provider=alias,
                secret_provider_command=None,
                provider_environment=base.provider_environment,
            )
            provider = load_secret_provider(config)
            self.assertIsInstance(provider, NamedSecretProvider)
            self.assertEqual(provider.describe(), expected_names[alias])

    def test_named_provider_caches_scope_exports_and_gets(self) -> None:
        provider = NamedSecretProvider("pass")
        ref = type(
            "Ref",
            (),
            {"scope": "shared", "vmid": None, "group": None, "cli_args": lambda self: ["--scope", "shared"]},
        )()

        with patch.object(provider.adapter, "export_scope", return_value={"alpha": "one"}) as export_mock:
            self.assertEqual(provider.export_scope(ref), {"alpha": "one"})
            self.assertEqual(provider.get(ref, "alpha"), "one")
            self.assertEqual(provider.list_names(ref), ["alpha"])
            self.assertTrue(provider.has_any(ref))

        export_mock.assert_called_once()

    def test_named_provider_applies_configured_environment(self) -> None:
        provider = NamedSecretProvider(
            "pass",
            extra_env={"PROXNIX_SECRET_PATH_PREFIX": "team/proxnix"},
        )

        observed: dict[str, str | None] = {}

        def fake_list(*, scope, vmid, group):
            observed["prefix"] = os.environ.get("PROXNIX_SECRET_PATH_PREFIX")
            return ["alpha"]

        with patch.object(provider.adapter, "list", side_effect=fake_list) as list_mock:
            provider.list_names(type("Ref", (), {"scope": "shared", "vmid": None, "group": None})())

        self.assertEqual(list_mock.call_count, 1)
        self.assertEqual(observed["prefix"], "team/proxnix")
        self.assertNotIn("PROXNIX_SECRET_PATH_PREFIX", os.environ)


class NamedAdapterTests(unittest.TestCase):
    class _FakeEntry:
        def __init__(self, title: str, password: str) -> None:
            self.title = title
            self.password = password

    class _FakeGroup:
        def __init__(self, name: str) -> None:
            self.name = name
            self.subgroups: list["NamedAdapterTests._FakeGroup"] = []
            self.entries: list["NamedAdapterTests._FakeEntry"] = []

    class _FakePyKeePass:
        def __init__(self) -> None:
            self.root_group = NamedAdapterTests._FakeGroup("/")
            self.saved = 0
            self.deleted: list[NamedAdapterTests._FakeEntry] = []

        def add_group(self, destination_group, group_name: str):
            group = NamedAdapterTests._FakeGroup(group_name)
            destination_group.subgroups.append(group)
            return group

        def add_entry(self, destination_group, title: str, username: str, password: str, url: str | None = None):
            entry = NamedAdapterTests._FakeEntry(title, password)
            destination_group.entries.append(entry)
            return entry

        def delete_entry(self, entry) -> None:
            self.deleted.append(entry)
            for group in self._all_groups():
                if entry in group.entries:
                    group.entries.remove(entry)
                    return

        def save(self) -> None:
            self.saved += 1

        def _all_groups(self):
            queue = [self.root_group]
            while queue:
                group = queue.pop(0)
                yield group
                queue.extend(group.subgroups)

    def test_pass_adapter_uses_expected_scope_paths_and_commands(self) -> None:
        adapter = PassAdapter()
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return CompletedProcess(args=args, returncode=0, stdout="alpha\nbeta\n", stderr="")

        with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
            names = adapter.list(scope="group", vmid=None, group="storage")
            adapter.set(scope="container", vmid="120", group=None, name="db_password", value="secret")
            adapter.remove(scope="shared", vmid=None, group=None, name="common_admin_password_hash")

        self.assertEqual(names, ["alpha", "beta"])
        self.assertEqual(calls[0], ["pass", "ls", "proxnix/groups/storage"])
        self.assertEqual(calls[1], ["pass", "insert", "-m", "-f", "proxnix/containers/120/db_password"])
        self.assertEqual(calls[2], ["pass", "rm", "-f", "proxnix/shared/common_admin_password_hash"])

    def test_gopass_adapter_uses_gopass_commands(self) -> None:
        adapter = GoPassAdapter()
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return CompletedProcess(args=args, returncode=0, stdout="alpha\n", stderr="")

        with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
            adapter.list(scope="shared", vmid=None, group=None)
            adapter.get(scope="shared", vmid=None, group=None, name="alpha")

        self.assertEqual(calls[0], ["gopass", "ls", "proxnix/shared"])
        self.assertEqual(calls[1], ["gopass", "show", "proxnix/shared/alpha"])

    def test_passhole_adapter_uses_eval_with_database_and_password_file(self) -> None:
        adapter = PassholeAdapter()
        env = {
            "PROXNIX_PASSHOLE_DATABASE": "/tmp/proxnix.kdbx",
            "PROXNIX_PASSHOLE_KEYFILE": "/tmp/proxnix.key",
            "PROXNIX_PASSHOLE_PASSWORD_FILE": "/tmp/passhole-password.txt",
            "PROXNIX_PASSHOLE_CACHE_TIMEOUT": "900",
        }
        calls: list[list[str]] = []
        inputs: list[str | None] = []
        responses = [
            CompletedProcess(args=["ph"], returncode=0, stdout='["alpha","beta"]\n', stderr=""),
            CompletedProcess(args=["ph"], returncode=0, stdout='"secret-value"\n', stderr=""),
            CompletedProcess(args=["ph"], returncode=0, stdout='{"alpha":"one","beta":"two"}\n', stderr=""),
            CompletedProcess(args=["ph"], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=["ph"], returncode=0, stdout="", stderr=""),
        ]

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            inputs.append(kwargs.get("input_text"))
            return responses.pop(0)

        with patch.dict("os.environ", env, clear=False):
            with patch("pathlib.Path.read_text", return_value="db-password"):
                with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
                    names = adapter.list(scope="shared", vmid=None, group=None)
                    value = adapter.get(scope="shared", vmid=None, group=None, name="alpha")
                    data = adapter.export_scope(scope="shared", vmid=None, group=None)
                    adapter.set(scope="container", vmid="120", group=None, name="api_key", value="new-secret")
                    adapter.remove(scope="shared", vmid=None, group=None, name="alpha")

        self.assertEqual(names, ["alpha", "beta"])
        self.assertEqual(value, "secret-value")
        self.assertEqual(data, {"alpha": "one", "beta": "two"})
        self.assertEqual(
            calls[0][:8],
            [
                "ph",
                "--database",
                "/tmp/proxnix.kdbx",
                "--keyfile",
                "/tmp/proxnix.key",
                "--password",
                "-",
                "--cache-timeout",
            ],
        )
        self.assertEqual(calls[0][8:11], ["900", "eval", "--json"])
        self.assertIn("kp.find_groups(path=[\"proxnix\", \"shared\"]", calls[0][11])
        self.assertEqual(
            calls[1][:11],
            [
                "ph",
                "--database",
                "/tmp/proxnix.kdbx",
                "--keyfile",
                "/tmp/proxnix.key",
                "--password",
                "-",
                "--cache-timeout",
                "900",
                "eval",
                "--json",
            ],
        )
        self.assertIn("kp.find_entries(path=[\"proxnix\", \"shared\", \"alpha\"]", calls[1][11])
        self.assertIn("kp.add_entry(group, \"api_key\", '', \"new-secret\"", calls[3][10])
        self.assertIn("entry.delete()", calls[4][10])
        self.assertEqual(inputs, ["db-password\n"] * 5)

    def test_pykeepass_adapter_reads_and_writes_database(self) -> None:
        adapter = PyKeePassAdapter()
        fake_kp = self._FakePyKeePass()
        proxnix = fake_kp.add_group(fake_kp.root_group, "proxnix")
        shared = fake_kp.add_group(proxnix, "shared")
        fake_kp.add_entry(shared, "alpha", "", "secret-value")

        with patch.object(adapter, "_open_database", return_value=fake_kp):
            names = adapter.list(scope="shared", vmid=None, group=None)
            value = adapter.get(scope="shared", vmid=None, group=None, name="alpha")
            data = adapter.export_scope(scope="shared", vmid=None, group=None)
            adapter.set(scope="group", vmid=None, group="app", name="api_key", value="new-secret")
            adapter.remove(scope="shared", vmid=None, group=None, name="alpha")

        self.assertEqual(names, ["alpha"])
        self.assertEqual(value, "secret-value")
        self.assertEqual(data, {"alpha": "secret-value"})
        groups_parent = next(group for group in proxnix.subgroups if group.name == "groups")
        app_group = next(group for group in groups_parent.subgroups if group.name == "app")
        self.assertEqual([(entry.title, entry.password) for entry in app_group.entries], [("api_key", "new-secret")])
        self.assertEqual(fake_kp.deleted[0].title, "alpha")
        self.assertEqual(fake_kp.saved, 2)

    def test_pykeepass_adapter_uses_agent_derived_password_when_configured(self) -> None:
        adapter = PyKeePassAdapter()
        with patch.dict(
            "os.environ",
            {
                "PROXNIX_PYKEEPASS_DATABASE": "/tmp/proxnix.kdbx",
                "PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY": "ssh-ed25519 AAAAagentkey",
            },
            clear=False,
        ):
            with patch(
                "proxnix_workstation.secret_provider_adapters.derive_pykeepass_agent_password",
                return_value="derived-password",
            ) as derive_mock:
                self.assertEqual(adapter._password(), "derived-password")

        derive_mock.assert_called_once_with("/tmp/proxnix.kdbx", "ssh-ed25519 AAAAagentkey")

    def test_pykeepass_adapter_opens_database_once_per_instance(self) -> None:
        adapter = PyKeePassAdapter()
        fake_kp = self._FakePyKeePass()

        with patch.object(adapter, "_pykeepass_class", return_value=lambda *args, **kwargs: fake_kp) as class_mock:
            with patch.dict(
                "os.environ",
                {"PROXNIX_PYKEEPASS_DATABASE": "/tmp/proxnix.kdbx"},
                clear=False,
            ):
                adapter.list(scope="shared", vmid=None, group=None)
                adapter.get(scope="shared", vmid=None, group=None, name="alpha")
                adapter.export_scope(scope="shared", vmid=None, group=None)

        class_mock.assert_called_once()

    def test_keepassxc_cli_adapter_uses_database_and_unlock_options(self) -> None:
        adapter = KeePassXCCliAdapter()
        calls: list[list[str]] = []
        env = {
            "PROXNIX_KEEPASSXC_DATABASE": "/tmp/proxnix.kdbx",
            "PROXNIX_KEEPASSXC_PASSWORD_FILE": "/tmp/kdbx-pass.txt",
            "PROXNIX_KEEPASSXC_KEY_FILE": "/tmp/kdbx.keyx",
        }

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return CompletedProcess(args=args, returncode=0, stdout="alpha\n", stderr="")

        with patch.dict("os.environ", env, clear=False):
            with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
                adapter.list(scope="shared", vmid=None, group=None)
                adapter.get(scope="shared", vmid=None, group=None, name="alpha")

        self.assertEqual(
            calls[0],
            [
                "keepassxc-cli",
                "ls",
                "--password-file",
                "/tmp/kdbx-pass.txt",
                "--key-file",
                "/tmp/kdbx.keyx",
                "/tmp/proxnix.kdbx",
                "proxnix/shared",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                "keepassxc-cli",
                "show",
                "-q",
                "-s",
                "-a",
                "password",
                "--password-file",
                "/tmp/kdbx-pass.txt",
                "--key-file",
                "/tmp/kdbx.keyx",
                "/tmp/proxnix.kdbx",
                "proxnix/shared/alpha",
            ],
        )

    def test_bws_adapter_creates_project_and_secret(self) -> None:
        adapter = BitwardenSecretsAdapter()
        calls: list[list[str]] = []
        responses = [
            CompletedProcess(args=["bws"], returncode=0, stdout="[]\n", stderr=""),
            CompletedProcess(
                args=["bws"],
                returncode=0,
                stdout='{"id":"proj-1","name":"proxnix/shared"}\n',
                stderr="",
            ),
            CompletedProcess(args=["bws"], returncode=0, stdout="[]\n", stderr=""),
            CompletedProcess(args=["bws"], returncode=0, stdout='{"id":"secret-1"}\n', stderr=""),
        ]

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return responses.pop(0)

        with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
            adapter.set(scope="shared", vmid=None, group=None, name="db_password", value="secret")

        self.assertEqual(calls[0], ["bws", "project", "list"])
        self.assertEqual(calls[1], ["bws", "project", "create", "proxnix/shared"])
        self.assertEqual(calls[2], ["bws", "secret", "list", "proj-1"])
        self.assertEqual(calls[3], ["bws", "secret", "create", "db_password", "secret", "proj-1"])

    def test_bws_adapter_exports_scope_from_project_list(self) -> None:
        adapter = BitwardenSecretsAdapter()
        responses = [
            CompletedProcess(
                args=["bws"],
                returncode=0,
                stdout='[{"id":"proj-1","name":"proxnix/containers/120"}]\n',
                stderr="",
            ),
            CompletedProcess(
                args=["bws"],
                returncode=0,
                stdout='[{"key":"alpha","value":"one"},{"key":"beta","value":"two"}]\n',
                stderr="",
            ),
        ]

        with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=responses):
            data = adapter.export_scope(scope="container", vmid="120", group=None)

        self.assertEqual(data, {"alpha": "one", "beta": "two"})

    def test_vault_kv_adapter_uses_mount_and_metadata_delete(self) -> None:
        adapter = VaultKvAdapter()
        calls: list[list[str]] = []
        env = {"PROXNIX_VAULT_MOUNT": "kv"}
        responses = [
            CompletedProcess(args=["vault"], returncode=0, stdout='["alpha","beta"]\n', stderr=""),
            CompletedProcess(args=["vault"], returncode=0, stdout="secret-value", stderr=""),
            CompletedProcess(args=["vault"], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=["vault"], returncode=0, stdout="", stderr=""),
        ]

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return responses.pop(0)

        with patch.dict("os.environ", env, clear=False):
            with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
                names = adapter.list(scope="group", vmid=None, group="storage")
                value = adapter.get(scope="group", vmid=None, group="storage", name="alpha")
                adapter.set(scope="group", vmid=None, group="storage", name="alpha", value="new-value")
                adapter.remove(scope="group", vmid=None, group="storage", name="alpha")

        self.assertEqual(names, ["alpha", "beta"])
        self.assertEqual(value, "secret-value")
        self.assertEqual(
            calls[0],
            ["vault", "kv", "list", "-format=json", "-mount=kv", "proxnix/groups/storage/"],
        )
        self.assertEqual(
            calls[1],
            ["vault", "kv", "get", "-field=value", "-mount=kv", "proxnix/groups/storage/alpha"],
        )
        self.assertEqual(
            calls[2],
            ["vault", "kv", "put", "-mount=kv", "proxnix/groups/storage/alpha", "value=-"],
        )
        self.assertEqual(
            calls[3],
            ["vault", "kv", "metadata", "delete", "-mount=kv", "proxnix/groups/storage/alpha"],
        )

    def test_onepassword_adapter_uses_tag_scoped_items(self) -> None:
        adapter = OnePasswordAdapter()
        env = {"PROXNIX_1PASSWORD_VAULT": "Engineering"}
        calls: list[list[str]] = []
        responses = [
            CompletedProcess(
                args=["op"],
                returncode=0,
                stdout='[{"id":"item-1","title":"db_password"}]\n',
                stderr="",
            ),
            CompletedProcess(
                args=["op"],
                returncode=0,
                stdout='{"fields":[{"id":"password","value":"secret-value"}]}\n',
                stderr="",
            ),
            CompletedProcess(args=["op"], returncode=0, stdout='[]\n', stderr=""),
            CompletedProcess(args=["op"], returncode=0, stdout='{"id":"created"}\n', stderr=""),
        ]

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return responses.pop(0)

        with patch.dict("os.environ", env, clear=False):
            with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
                value = adapter.get(scope="shared", vmid=None, group=None, name="db_password")
                adapter.set(scope="container", vmid="120", group=None, name="api_key", value="new-secret")

        self.assertEqual(value, "secret-value")
        self.assertEqual(
            calls[0],
            [
                "op",
                "item",
                "list",
                "--vault",
                "Engineering",
                "--tags",
                "proxnix/shared",
                "--format",
                "json",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                "op",
                "item",
                "get",
                "item-1",
                "--vault",
                "Engineering",
                "--format",
                "json",
            ],
        )
        self.assertEqual(
            calls[2],
            [
                "op",
                "item",
                "list",
                "--vault",
                "Engineering",
                "--tags",
                "proxnix/containers/120",
                "--format",
                "json",
            ],
        )
        self.assertEqual(
            calls[3],
            [
                "op",
                "item",
                "create",
                "--category",
                "Password",
                "--title",
                "api_key",
                "--vault",
                "Engineering",
                "--tags",
                "proxnix/containers/120",
                "password=new-secret",
            ],
        )

    def test_infisical_adapter_uses_export_and_crud_commands(self) -> None:
        adapter = InfisicalAdapter()
        env = {
            "PROXNIX_INFISICAL_PROJECT_ID": "proj-123",
            "PROXNIX_INFISICAL_ENV": "prod",
            "PROXNIX_INFISICAL_TYPE": "shared",
        }
        calls: list[list[str]] = []
        responses = [
            CompletedProcess(args=["infisical"], returncode=0, stdout='{"alpha":"one","beta":"two"}\n', stderr=""),
            CompletedProcess(args=["infisical"], returncode=0, stdout="secret-value\n", stderr=""),
            CompletedProcess(args=["infisical"], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=["infisical"], returncode=0, stdout="", stderr=""),
        ]

        def fake_run_command(args, **kwargs):
            calls.append(list(args))
            return responses.pop(0)

        with patch.dict("os.environ", env, clear=False):
            with patch("proxnix_workstation.secret_provider_adapters.run_command", side_effect=fake_run_command):
                names = adapter.list(scope="group", vmid=None, group="storage")
                value = adapter.get(scope="group", vmid=None, group="storage", name="alpha")
                adapter.set(scope="group", vmid=None, group="storage", name="alpha", value="new-value")
                adapter.remove(scope="group", vmid=None, group="storage", name="alpha")

        self.assertEqual(names, ["alpha", "beta"])
        self.assertEqual(value, "secret-value")
        self.assertEqual(
            calls[0],
            [
                "infisical",
                "export",
                "--format=json",
                "--projectId",
                "proj-123",
                "--env",
                "prod",
                "--path",
                "/proxnix/groups/storage",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                "infisical",
                "secrets",
                "get",
                "alpha",
                "--plain",
                "--silent",
                "--projectId",
                "proj-123",
                "--env",
                "prod",
                "--path",
                "/proxnix/groups/storage",
            ],
        )
        self.assertEqual(
            calls[2],
            [
                "infisical",
                "secrets",
                "set",
                "alpha=new-value",
                "--type",
                "shared",
                "--projectId",
                "proj-123",
                "--env",
                "prod",
                "--path",
                "/proxnix/groups/storage",
            ],
        )
        self.assertEqual(
            calls[3],
            [
                "infisical",
                "secrets",
                "delete",
                "alpha",
                "--projectId",
                "proj-123",
                "--env",
                "prod",
                "--path",
                "/proxnix/groups/storage",
            ],
        )


class ConfigProviderEnvironmentTests(unittest.TestCase):
    def test_load_workstation_config_preserves_provider_settings_from_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(
                "\n".join(
                    [
                        "PROXNIX_SECRET_PROVIDER='passhole'",
                        "PROXNIX_PASSHOLE_DATABASE='~/secrets/proxnix.kdbx'",
                        "PROXNIX_SECRET_PATH_PREFIX='team/proxnix'",
                        "VAULT_ADDR='https://vault.example.test'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_workstation_config(
                config_path,
                environ={"HOME": "/home/tester"},
            )

        self.assertEqual(config.secret_provider, "passhole")
        self.assertEqual(
            config.provider_environment_map(),
            {
                "PROXNIX_PASSHOLE_DATABASE": "/home/tester/secrets/proxnix.kdbx",
                "PROXNIX_SECRET_PATH_PREFIX": "team/proxnix",
                "PROXNIX_SOPS_MASTER_IDENTITY": "/home/tester/.ssh/id_ed25519",
                "VAULT_ADDR": "https://vault.example.test",
            },
        )

    def test_generated_config_file_re_emits_provider_environment(self) -> None:
        source = WorkstationConfig(
            config_file=Path("/tmp/source-config"),
            site_dir=Path("/tmp/source-site"),
            hosts=("root@node1",),
            ssh_identity=None,
            remote_dir=PurePosixPath("/var/lib/proxnix"),
            remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
            remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
            secret_provider="passhole",
            secret_provider_command=None,
            provider_environment=(
                ("PROXNIX_SOPS_MASTER_IDENTITY", "/tmp/id_master"),
                ("PROXNIX_PASSHOLE_DATABASE", "/tmp/proxnix.kdbx"),
                ("VAULT_ADDR", "https://vault.example.test"),
            ),
        )

        generated = build_generated_config(
            source,
            config_path=Path("/tmp/generated-config"),
            site_dir=Path("/tmp/generated-site"),
            host="root@test-host",
        )
        rendered = render_config_file(generated)

        self.assertIn("PROXNIX_PASSHOLE_DATABASE=/tmp/proxnix.kdbx", rendered)
        self.assertIn("VAULT_ADDR=https://vault.example.test", rendered)


class PublishSecretProviderTests(unittest.TestCase):
    def test_build_compiled_secret_store_merges_provider_scopes_with_expected_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir) / "site"
            private_dir = site_dir / "private" / "containers" / "120"
            private_dir.mkdir(parents=True, exist_ok=True)
            (site_dir / "containers" / "120").mkdir(parents=True, exist_ok=True)
            (site_dir / "containers" / "120" / "secret-groups.list").write_text("app\n", encoding="utf-8")
            (private_dir / "age_identity.sops.yaml").write_text("identity: value\n", encoding="utf-8")
            config = _test_config(site_dir=site_dir)
            site_paths = SitePaths.from_config(config)
            provider = _FakeProvider(
                {
                    ("shared", None): {"a": "shared-a", "override": "shared"},
                    ("group", "app"): {"b": "group-b", "override": "group"},
                    ("container", "120"): {"c": "container-c", "override": "container"},
                }
            )
            out_dir = Path(temp_dir) / "out"
            captured: dict[str, str] = {}

            def fake_encrypt_json_to_file(config, source_json, recipients, destination, **kwargs):
                captured["json"] = source_json.read_text(encoding="utf-8")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("encrypted\n", encoding="utf-8")

            with patch("proxnix_workstation.publish_cli.have_container_private_key", return_value=True):
                with patch("proxnix_workstation.publish_cli.container_public_key", return_value="ssh-ed25519 AAAA"):
                    with patch("proxnix_workstation.publish_cli.master_private_key_text", return_value="master-private-key"):
                        with patch("proxnix_workstation.publish_cli.master_recipient", return_value="ssh-ed25519 BBBB"):
                            with patch(
                                "proxnix_workstation.publish_cli.sops_encrypt_json_to_file",
                                side_effect=fake_encrypt_json_to_file,
                            ):
                                build_compiled_secret_store(config, site_paths, provider, "120", out_dir)

        self.assertIn('"a": "shared-a"', captured["json"])
        self.assertIn('"b": "group-b"', captured["json"])
        self.assertIn('"c": "container-c"', captured["json"])
        self.assertIn('"override": "container"', captured["json"])

    def test_build_compiled_secret_store_rejects_ambiguous_group_secret_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir) / "site"
            private_dir = site_dir / "private" / "containers" / "120"
            private_dir.mkdir(parents=True, exist_ok=True)
            (site_dir / "containers" / "120").mkdir(parents=True, exist_ok=True)
            (site_dir / "containers" / "120" / "secret-groups.list").write_text("app\nops\n", encoding="utf-8")
            (private_dir / "age_identity.sops.yaml").write_text("identity: value\n", encoding="utf-8")
            config = _test_config(site_dir=site_dir)
            site_paths = SitePaths.from_config(config)
            provider = _FakeProvider(
                {
                    ("group", "app"): {"dup": "one"},
                    ("group", "ops"): {"dup": "two"},
                }
            )

            with self.assertRaises(ProxnixWorkstationError) as ctx:
                build_compiled_secret_store(config, site_paths, provider, "120", Path(temp_dir) / "out")

        self.assertIn("grouped secret dup is ambiguous", str(ctx.exception))


class KeePassAgentTests(unittest.TestCase):
    def test_available_pykeepass_agent_public_keys_uses_explicit_agent_socket(self) -> None:
        response = CompletedProcess(
            args=["ssh-add"],
            returncode=0,
            stdout="ssh-ed25519 AAAAfirst comment-one\n",
            stderr="",
        )

        with patch.dict(
            "os.environ",
            {"PROXNIX_PYKEEPASS_AGENT_SOCKET": "/tmp/proxnix-agent.sock"},
            clear=True,
        ):
            with patch("proxnix_workstation.keepass_agent.run_command", return_value=response) as run_mock:
                keys = available_pykeepass_agent_public_keys()

        self.assertEqual(keys, ["ssh-ed25519 AAAAfirst"])
        self.assertEqual(
            run_mock.call_args.kwargs["env"]["SSH_AUTH_SOCK"],
            "/tmp/proxnix-agent.sock",
        )

    def test_available_pykeepass_agent_public_keys_filters_to_ed25519(self) -> None:
        response = CompletedProcess(
            args=["ssh-add"],
            returncode=0,
            stdout=(
                "ssh-ed25519 AAAAfirst comment-one\n"
                "ssh-rsa AAAArsa comment-two\n"
                "ssh-ed25519 AAAAsecond comment-three\n"
            ),
            stderr="",
        )

        with patch.dict("os.environ", {"SSH_AUTH_SOCK": "/tmp/agent.sock"}, clear=False):
            with patch("proxnix_workstation.keepass_agent.run_command", return_value=response):
                keys = available_pykeepass_agent_public_keys()

        self.assertEqual(keys, ["ssh-ed25519 AAAAfirst", "ssh-ed25519 AAAAsecond"])

    def test_derive_pykeepass_agent_password_uses_agent_signature(self) -> None:
        responses = [
            CompletedProcess(
                args=["ssh-add"],
                returncode=0,
                stdout="ssh-ed25519 AAAAfirst comment-one\n",
                stderr="",
            ),
            CompletedProcess(
                args=["ssh-keygen"],
                returncode=0,
                stdout="-----BEGIN SSH SIGNATURE-----\nsignature-bytes\n-----END SSH SIGNATURE-----\n",
                stderr="",
            ),
        ]

        with patch.dict("os.environ", {"SSH_AUTH_SOCK": "/tmp/agent.sock"}, clear=False):
            with patch("proxnix_workstation.keepass_agent.ensure_commands"):
                with patch("proxnix_workstation.keepass_agent.run_command", side_effect=responses) as run_mock:
                    first = derive_pykeepass_agent_password("/tmp/proxnix.kdbx", "ssh-ed25519 AAAAfirst")

        with patch.dict("os.environ", {"SSH_AUTH_SOCK": "/tmp/agent.sock"}, clear=False):
            with patch("proxnix_workstation.keepass_agent.ensure_commands"):
                with patch(
                    "proxnix_workstation.keepass_agent.run_command",
                    side_effect=[
                        CompletedProcess(
                            args=["ssh-add"],
                            returncode=0,
                            stdout="ssh-ed25519 AAAAfirst another-comment\n",
                            stderr="",
                        ),
                        CompletedProcess(
                            args=["ssh-keygen"],
                            returncode=0,
                            stdout="-----BEGIN SSH SIGNATURE-----\nsignature-bytes\n-----END SSH SIGNATURE-----\n",
                            stderr="",
                        ),
                    ],
                ):
                    second = derive_pykeepass_agent_password("/tmp/proxnix.kdbx", "ssh-ed25519 AAAAfirst")

        self.assertEqual(first, second)
        self.assertEqual(run_mock.call_args_list[1].args[0][:5], ["ssh-keygen", "-Y", "sign", "-n", "proxnix-keepass-unlock@proxnix"])

    def test_print_keepass_password_uses_configured_pykeepass_agent_key(self) -> None:
        config = WorkstationConfig(
            config_file=Path("/tmp/source-config"),
            site_dir=Path("/tmp/source-site"),
            hosts=("root@node1",),
            ssh_identity=None,
            remote_dir=PurePosixPath("/var/lib/proxnix"),
            remote_priv_dir=PurePosixPath("/var/lib/proxnix/private"),
            remote_host_relay_identity=PurePosixPath("/etc/proxnix/host_relay_identity"),
            secret_provider="pykeepass",
            secret_provider_command=None,
            provider_environment=(
                ("PROXNIX_PYKEEPASS_AGENT_SOCKET", "/tmp/strongbox-agent.sock"),
                ("PROXNIX_PYKEEPASS_DATABASE", "/tmp/proxnix.kdbx"),
                ("PROXNIX_PYKEEPASS_AGENT_PUBLIC_KEY", "ssh-ed25519 AAAAfirst"),
            ),
        )

        with patch("builtins.print") as print_mock:
            with patch(
                "proxnix_workstation.secrets_cli.derive_pykeepass_agent_password",
                side_effect=lambda database, key: (
                    self.assertEqual(os.environ["PROXNIX_PYKEEPASS_AGENT_SOCKET"], "/tmp/strongbox-agent.sock")
                    or "derived-password"
                ),
            ) as derive_mock:
                result = cmd_print_keepass_password(config)

        self.assertEqual(result, 0)
        derive_mock.assert_called_once_with("/tmp/proxnix.kdbx", "ssh-ed25519 AAAAfirst")
        print_mock.assert_called_once_with("derived-password")


if __name__ == "__main__":
    unittest.main()
