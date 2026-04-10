#!/usr/bin/env python3
"""yaml-to-nix.py — Convert PVE conf + optional proxmox.yaml to NixOS .nix files.

Uses only Python 3 stdlib so it runs on a stock Proxmox host (Debian-based).
Is idempotent: files are only written when their content changes, so the
systemd path-watcher inside the container is not triggered spuriously.

Usage:
    python3 yaml-to-nix.py --pve-conf PATH
                            [--proxmox-yaml PATH] [--user-yaml PATH]
                            [--out-dir DIR]

--pve-conf is required for generating proxmox.nix.  It provides hostname,
IP, gateway, and DNS from the Proxmox WebUI — the authoritative source.

--proxmox-yaml is optional.  It can only ADD fields that PVE conf does not
define (e.g. search_domain).  It cannot override PVE values.

Merge order (last wins): proxmox.yaml → PVE conf
"""

import sys
import os
import argparse
import textwrap
import urllib.parse

# ── Minimal YAML parser (stdlib only) ────────────────────────────────────────
# Handles the subset we need: mappings, sequences, scalars, quoted strings.
# No anchors, no multi-line strings, no flow style.

class _YAMLParser:
    def __init__(self, text):
        self._lines = []
        for raw in text.splitlines():
            stripped = raw.rstrip()
            if stripped and not stripped.lstrip().startswith('#'):
                indent = len(raw) - len(raw.lstrip(' '))
                self._lines.append((indent, stripped.lstrip()))
        self._pos = 0

    # ── internal helpers ──────────────────────────────────────────────────────

    def _peek(self):
        return self._lines[self._pos] if self._pos < len(self._lines) else None

    def _done(self):
        return self._pos >= len(self._lines)

    def _parse_block(self, indent):
        """Dispatch to mapping or sequence parser at *indent*."""
        line = self._peek()
        if line is None or line[0] != indent:
            return None
        if line[1].startswith('- '):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent):
        result = {}
        while not self._done():
            line_indent, content = self._peek()
            if line_indent < indent:
                break
            if line_indent > indent:
                break
            if content.startswith('- '):
                break
            if ': ' not in content and not content.endswith(':'):
                break  # not a mapping line

            self._pos += 1

            if ': ' in content:
                key, _, rest = content.partition(': ')
                key = key.strip()
                if rest:
                    result[key] = self._parse_scalar(rest)
                else:
                    # value is a block on the next line(s)
                    nxt = self._peek()
                    if nxt and nxt[0] > indent:
                        result[key] = self._parse_block(nxt[0])
                    else:
                        result[key] = None
            else:
                # "key:" with block value
                key = content[:-1].strip()
                nxt = self._peek()
                if nxt and nxt[0] > indent:
                    result[key] = self._parse_block(nxt[0])
                else:
                    result[key] = None
        return result

    def _parse_sequence(self, indent):
        result = []
        while not self._done():
            line_indent, content = self._peek()
            if line_indent != indent or not content.startswith('- '):
                break
            self._pos += 1
            item_text = content[2:]  # strip leading "- "

            if not item_text:
                # block item on next lines
                nxt = self._peek()
                if nxt and nxt[0] > indent:
                    result.append(self._parse_block(nxt[0]))
                else:
                    result.append(None)
            elif ': ' in item_text or item_text.endswith(':'):
                # first key-value of an inline mapping
                item = {}
                if ': ' in item_text:
                    k, _, v = item_text.partition(': ')
                    item[k.strip()] = self._parse_scalar(v)
                else:
                    k = item_text[:-1].strip()
                    nxt = self._peek()
                    if nxt and nxt[0] > indent:
                        item[k] = self._parse_block(nxt[0])
                    else:
                        item[k] = None
                # continue the mapping at whatever indent follows
                nxt = self._peek()
                if nxt and nxt[0] > indent:
                    rest = self._parse_mapping(nxt[0])
                    if isinstance(rest, dict):
                        item.update(rest)
                result.append(item)
            else:
                result.append(self._parse_scalar(item_text))
        return result

    @staticmethod
    def _parse_scalar(s):
        s = s.strip()
        if s in ('true', 'True', 'yes'):
            return True
        if s in ('false', 'False', 'no'):
            return False
        if s in ('null', '~'):
            return None
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
        if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
            return s[1:-1]
        return s

    def parse(self):
        if not self._lines:
            return {}
        return self._parse_block(self._lines[0][0])


def load_yaml(path):
    with open(path) as fh:
        return _YAMLParser(fh.read()).parse()


# ── Nix quoting helpers ───────────────────────────────────────────────────────

def nix_str(s):
    """Return a Nix string literal for *s*, escaping the minimum needed."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('${', '\\${') + '"'

def nix_str_list(items):
    """Return a Nix list of string literals on one line."""
    return '[ ' + ' '.join(nix_str(i) for i in items) + ' ]'

def nix_val(v):
    """Convert a Python scalar to its Nix literal representation."""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if v is None:
        return 'null'
    return nix_str(str(v))

def nix_indent(text, spaces=2):
    return textwrap.indent(text, ' ' * spaces)


def decode_ssh_public_keys(value):
    """Decode ssh-public-keys from PVE conf or proxmox.yaml."""
    if not value:
        return []
    if isinstance(value, list):
        raw = '\n'.join(str(item) for item in value)
    else:
        raw = str(value)
    decoded = urllib.parse.unquote(raw).replace('\\n', '\n')
    return [
        line.strip() for line in decoded.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]


# ── PVE conf parser ───────────────────────────────────────────────────────────

def parse_pve_conf(path):
    """Extract networking config from /etc/pve/lxc/<vmid>.conf.

    Translates the same fields that PVE::LXC::Setup applies to Debian
    containers, covering all netN options.  Returns a dict that can be
    merged directly with proxmox.yaml data.

    PVE conf format:
      hostname: example-container
      net0: name=eth0,bridge=vmbr0,ip=192.0.2.10/24,gw=192.0.2.1,
            ip6=2001:db8::1/64,gw6=2001:db8::1,type=veth,...
      net1: name=eth1,bridge=vmbr1,ip=dhcp,ip6=auto,...
      nameserver: 8.8.8.8 1.1.1.1
      searchdomain: example.internal

    IPv6 modes (ip6= field):
      <addr>/<prefix>  static address
      auto             SLAAC  (kernel accept_ra + autoconf)
      dhcp             DHCPv6 (kernel accept_ra with managed flag)
      (absent)         IPv6 not configured on this interface
    """
    raw = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('['):
                continue
            if ': ' in line:
                key, _, val = line.partition(': ')
                raw[key.strip()] = val.strip()

    data = {}

    if 'hostname' in raw:
        data['hostname'] = raw['hostname']

    # Parse every netN interface in declaration order.
    interfaces = []
    for key in sorted(raw):
        if not (key.startswith('net') and key[3:].isdigit()):
            continue
        params = dict(
            part.split('=', 1) for part in raw[key].split(',') if '=' in part
        )

        iface = {'name': params.get('name', 'eth' + key[3:])}

        # IPv4 — static or DHCP
        ip_cidr = params.get('ip', '')
        if ip_cidr == 'dhcp':
            iface['dhcp4'] = True
        elif ip_cidr and '/' in ip_cidr:
            addr, prefix = ip_cidr.rsplit('/', 1)
            iface['ip']     = addr
            iface['prefix'] = int(prefix)

        # IPv4 default gateway — first interface that declares one wins
        gw = params.get('gw')
        if gw and 'gateway' not in data:
            data['gateway'] = gw

        # IPv6 — static, SLAAC, or DHCPv6
        ip6 = params.get('ip6', '')
        if ip6 == 'auto':
            iface['slaac'] = True
        elif ip6 == 'dhcp':
            iface['dhcp6'] = True
        elif ip6 and '/' in ip6:
            addr6, prefix6 = ip6.rsplit('/', 1)
            iface['ip6']     = addr6
            iface['prefix6'] = int(prefix6)

        # IPv6 default gateway — first interface that declares one wins
        gw6 = params.get('gw6')
        if gw6 and 'gateway6' not in data:
            data['gateway6'] = gw6

        interfaces.append(iface)

    if interfaces:
        data['interfaces'] = interfaces

    if 'nameserver' in raw:
        servers = raw['nameserver'].split()
        if servers:
            data['dns'] = servers

    if 'searchdomain' in raw:
        data['search_domain'] = raw['searchdomain']

    if 'ssh-public-keys' in raw:
        keys = decode_ssh_public_keys(raw['ssh-public-keys'])
        if keys:
            data['ssh_keys'] = keys

    return data


# ── proxmox.nix generator ─────────────────────────────────────────────────────

def generate_proxmox_nix(data):
    """Generate proxmox.nix from merged PVE conf + proxmox.yaml data."""
    hostname      = data.get('hostname')
    dns           = data.get('dns', [])
    search_domain = data.get('search_domain')
    gateway       = data.get('gateway')
    gateway6      = data.get('gateway6')
    ssh_keys      = decode_ssh_public_keys(data.get('ssh_keys', []))

    lines = [
        "# Generated by yaml-to-nix.py — do not edit by hand.",
        "{ ... }: {",
    ]

    if hostname:
        lines.append(f"  networking.hostName = {nix_str(hostname)};")
    if ssh_keys:
        lines.append(f"  users.users.root.openssh.authorizedKeys.keys = {nix_str_list(ssh_keys)};")

    if 'interfaces' in data:
        lines.append("  networking.useDHCP = false;")

        for iface in data['interfaces']:
            name = iface['name']
            lines.append("")

            if iface.get('dhcp4'):
                lines.append(f"  networking.interfaces.{name}.useDHCP = true;")
            elif 'ip' in iface:
                lines.append(f"  networking.interfaces.{name}.ipv4.addresses = [{{")
                lines.append(f"    address = {nix_str(iface['ip'])};")
                lines.append(f"    prefixLength = {iface['prefix']};")
                lines.append("  }];")

            if 'ip6' in iface:
                lines.append(f"  networking.interfaces.{name}.ipv6.addresses = [{{")
                lines.append(f"    address = {nix_str(iface['ip6'])};")
                lines.append(f"    prefixLength = {iface['prefix6']};")
                lines.append("  }];")
            elif iface.get('slaac'):
                # SLAAC: enable RA reception and stateless autoconf via sysctl
                lines.append(f'  boot.kernel.sysctl."net.ipv6.conf.{name}.accept_ra" = 1;')
                lines.append(f'  boot.kernel.sysctl."net.ipv6.conf.{name}.autoconf" = 1;')
            elif iface.get('dhcp6'):
                # DHCPv6: accept RA with managed flag so dhclient picks up address
                lines.append(f'  boot.kernel.sysctl."net.ipv6.conf.{name}.accept_ra" = 2;')

        if gateway:
            lines.append("")
            lines.append(f"  networking.defaultGateway = {nix_str(gateway)};")
        if gateway6:
            lines.append(f"  networking.defaultGateway6 = {nix_str(gateway6)};")

    if dns:
        lines.append(f"  networking.nameservers = {nix_str_list(dns)};")
    if search_domain:
        lines.append(f"  networking.search = [ {nix_str(search_domain)} ];")

    lines.append("}")
    return '\n'.join(lines) + '\n'


# ── user.nix generators ───────────────────────────────────────────────────────

# ── secret helpers ────────────────────────────────────────────────────────────

def normalize_secret(s):
    """Convert a secret spec to a Quadlet Secret= string.

    Accepts either:
      - a raw string: "db_password,type=env,target=DB_PASSWORD"
      - a structured dict: {name: db_password, target: DB_PASSWORD, type: env}

    type defaults to "env". target is required for structured form.
    """
    if isinstance(s, str):
        return s
    name   = s['name']
    stype  = s.get('type', 'env')
    parts  = [name, f'type={stype}']
    if 'target' in s:
        parts.append(f'target={s["target"]}')
    return ','.join(parts)


def secret_name(s):
    """Extract the secret name from a raw or structured spec."""
    return s if isinstance(s, str) else s['name']


def age_decrypt_line(name, outpath):
    """Return a NixOS ExecStartPre string that decrypts one age secret.

    The leading '+' makes systemd run it as root regardless of the service's
    User= setting, so it can read /etc/age/identity.txt (mode 600, root only).
    The output file is written to the service's RuntimeDirectory.
    """
    return (
        f'"+${{pkgs.age}}/bin/age --decrypt --identity /etc/age/identity.txt'
        f' --output {outpath} /etc/secrets/{name}.age"'
    )


# ── user.nix generators ───────────────────────────────────────────────────────

def generate_user_nix_podman(data):
    """Generate user.nix using the built-in oci-containers module.

    YAML field → virtualisation.oci-containers.containers attribute:
      ports    → ports         (list of "host:container" strings)
      volumes  → volumes       (list of "host:container[:opts]" strings)
      env      → environment   (attrsOf str)
      secrets  → extraOptions  (--secret=... Podman flags)
      restart  → systemd.services."podman-<name>".serviceConfig.Restart

    Secrets are delivered by Podman using the age shell driver registered
    by the pre-start hook. No plaintext secret file is ever written.
    """
    containers = data.get('containers', [])
    restart_units = []

    lines = []
    lines.append("# Generated by yaml-to-nix.py from user.yaml — do not edit by hand.")
    lines.append("{ ... }: {")
    lines.append('  virtualisation.oci-containers.backend = "podman";')
    lines.append("  virtualisation.oci-containers.containers = {")

    for ctr in containers:
        name    = ctr['name']
        image   = ctr['image']
        ports   = ctr.get('ports', [])
        vols    = ctr.get('volumes', [])
        env     = ctr.get('env', {}) or {}
        secrets = [normalize_secret(s) for s in ctr.get('secrets', [])]
        restart = ctr.get('restart', None)

        lines.append(f'    "{name}" = {{')
        lines.append(f"        image = {nix_str(image)};")

        if ports:
            lines.append(f"        ports = {nix_str_list(ports)};")
        if vols:
            lines.append(f"        volumes = {nix_str_list(vols)};")
        if env:
            lines.append("        environment = {")
            for k, v in env.items():
                lines.append(f"          {k} = {nix_str(v)};")
            lines.append("        };")
        if secrets:
            lines.append(
                f"        extraOptions = {nix_str_list([f'--secret={secret}' for secret in secrets])};"
            )

        if restart:
            restart_units.append((name, restart))

        lines.append("    };")

    lines.append("  };")
    for name, restart in restart_units:
        lines.append(f'  systemd.services."podman-{name}".serviceConfig.Restart = {nix_str(restart)};')
    lines.append("}")
    return '\n'.join(lines) + '\n'


def generate_user_nix_empty():
    return (
        "# Generated by yaml-to-nix.py — no user.yaml provided.\n"
        "{ ... }: {\n"
        "}\n"
    )


def emit_service_secrets(lines, svcname, secrets):
    """Emit tmpfiles + ExecStartPre for native-service age secrets.

    Each secret is decrypted from /etc/secrets/<name>.age into the service's
    RuntimeDirectory (/run/<svcname>-secrets/<name>) at service-start time.
    The '+' prefix on ExecStartPre runs the command as root so it can read
    /etc/age/identity.txt (mode 600).  The output file lands in a tmpfs path
    that is removed when the service stops.

    The service is responsible for pointing its config at these paths (e.g.
    services.immich.database.passwordFile). Do that in a dropin .nix file or
    directly in user.yaml if the option is supported.
    """
    if not secrets:
        return
    rtdir = f"/run/{svcname}-secrets"
    lines.append(f"  # Age-encrypted secrets for {svcname}")
    lines.append(f'  systemd.tmpfiles.rules = [ "d {rtdir} 0700 root root -" ];')
    lines.append(f"  systemd.services.{svcname}.serviceConfig.ExecStartPre = [")
    for s in secrets:
        name    = secret_name(s)
        outpath = s.get('path', f'{rtdir}/{name}') if isinstance(s, dict) else f'{rtdir}/{name}'
        lines.append(f"    {age_decrypt_line(name, outpath)}")
    lines.append("  ];")
    lines.append("")


def generate_user_nix_native(data):
    """Generate user.nix for native NixOS services — fully generic.

    Each entry under services: in user.yaml maps to services.<name>.enable = true
    plus optional modifiers:

      hardware_acceleration: true
        → users.users.<name>.extraGroups = ["render" "video"]
        → systemd.services.<name>.serviceConfig.PrivateDevices = lib.mkForce false

      unstable_package: true
        → injects nixpkgs.config.packageOverrides overlay
        → services.<name>.package = pkgs.unstable.<name>

      options:
        <nixOptionName>: <value>
        → passed through as services.<name>.<nixOptionName> = <value>
        Use this for any service-specific NixOS option (e.g. mediaLocation,
        port, openFirewall, …).

      secrets:
        - name: <secret-name>
          path: /run/<name>-secrets/<secret-name>   # optional, default shown
        → systemd.tmpfiles + ExecStartPre age decrypt
    """
    services = data.get('services', {}) or {}

    need_unstable = any(
        (cfg or {}).get('unstable_package', False)
        for cfg in services.values()
    )

    lines = []
    lines.append("# Generated by yaml-to-nix.py from user.yaml — do not edit by hand.")

    if need_unstable:
        lines.append("{ config, lib, pkgs, ... }:")
        lines.append("")
        lines.append("let")
        lines.append("  unstableTarball = fetchTarball")
        lines.append('    "https://github.com/NixOS/nixpkgs/archive/nixos-unstable.tar.gz";')
        lines.append("in {")
        lines.append("  nixpkgs.config.packageOverrides = pkgs: {")
        lines.append("    unstable = import unstableTarball {")
        lines.append("      config = config.nixpkgs.config;")
        lines.append("    };")
        lines.append("  };")
        lines.append("")
    else:
        lines.append("{ config, lib, pkgs, ... }: {")
        lines.append("")

    for svcname, cfg in services.items():
        cfg = cfg or {}
        if not cfg.get('enable', False):
            continue

        options     = cfg.get('options', {}) or {}
        use_unstable = cfg.get('unstable_package', False)
        hw_accel    = cfg.get('hardware_acceleration', False)
        secrets     = cfg.get('secrets', [])

        # services.<name> block
        if options or use_unstable:
            lines.append(f"  services.{svcname} = {{")
            lines.append(f"    enable = true;")
            for opt, val in options.items():
                lines.append(f"    {opt} = {nix_val(val)};")
            if use_unstable:
                lines.append(f"    package = pkgs.unstable.{svcname};")
            lines.append("  };")
        else:
            lines.append(f"  services.{svcname}.enable = true;")

        if hw_accel:
            lines.append(f'  users.users.{svcname}.extraGroups = [ "render" "video" ];')
            lines.append(f"  systemd.services.{svcname}.serviceConfig.PrivateDevices = lib.mkForce false;")

        emit_service_secrets(lines, svcname, secrets)

        if not secrets:
            lines.append("")

    lines.append("}")
    return '\n'.join(lines) + '\n'


def generate_user_nix(data):
    if data.get('podman', True):
        return generate_user_nix_podman(data)
    return generate_user_nix_native(data)


# ── file writing (idempotent) ─────────────────────────────────────────────────

def write_if_changed(path, content):
    if os.path.exists(path):
        with open(path) as fh:
            if fh.read() == content:
                print(f"  unchanged: {path}", flush=True)
                return
    with open(path, 'w') as fh:
        fh.write(content)
    print(f"  written:   {path}", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Convert PVE conf / proxmox.yaml / user.yaml to NixOS .nix files."
    )
    ap.add_argument('--pve-conf', default=None,
                    help="Path to /etc/pve/lxc/<vmid>.conf (required for proxmox.nix)")
    ap.add_argument('--proxmox-yaml', default=None,
                    help="Path to proxmox.yaml — optional; adds fields absent "
                         "from PVE conf (e.g. search_domain)")
    ap.add_argument('--user-yaml', default=None,
                    help="Path to user.yaml (skipped if not given)")
    ap.add_argument('--out-dir', default='/etc/nixos',
                    help="Directory to write .nix files into (default: /etc/nixos)")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    errors = 0

    # ── proxmox.nix ───────────────────────────────────────────────────────────
    # PVE conf is the authoritative source for networking.  proxmox.yaml is
    # optional and can only add fields absent from PVE conf (e.g. search_domain).
    if args.pve_conf:
        pve_data  = {}
        yaml_data = {}

        if not os.path.exists(args.pve_conf):
            print(f"ERROR: --pve-conf {args.pve_conf} not found", file=sys.stderr)
            errors += 1
        else:
            try:
                pve_data = parse_pve_conf(args.pve_conf)
            except Exception as e:
                print(f"ERROR parsing PVE conf: {e}", file=sys.stderr)
                errors += 1

        if args.proxmox_yaml:
            if not os.path.exists(args.proxmox_yaml):
                print(f"WARNING: {args.proxmox_yaml} not found, skipping",
                      file=sys.stderr)
            else:
                try:
                    yaml_data = load_yaml(args.proxmox_yaml)
                except Exception as e:
                    print(f"ERROR parsing proxmox.yaml: {e}", file=sys.stderr)
                    errors += 1

        if not errors:
            merged = {**yaml_data, **pve_data}
            try:
                nix = generate_proxmox_nix(merged)
                write_if_changed(os.path.join(args.out_dir, 'proxmox.nix'), nix)
            except Exception as e:
                print(f"ERROR generating proxmox.nix: {e}", file=sys.stderr)
                errors += 1

    # ── user.nix ──────────────────────────────────────────────────────────────
    if args.user_yaml:
        if not os.path.exists(args.user_yaml):
            print(f"ERROR: {args.user_yaml} not found", file=sys.stderr)
            errors += 1
        else:
            try:
                data = load_yaml(args.user_yaml)
                nix  = generate_user_nix(data)
                write_if_changed(os.path.join(args.out_dir, 'user.nix'), nix)
            except Exception as e:
                print(f"ERROR generating user.nix: {e}", file=sys.stderr)
                errors += 1
    else:
        write_if_changed(os.path.join(args.out_dir, 'user.nix'), generate_user_nix_empty())

    if errors:
        sys.exit(1)

if __name__ == '__main__':
    main()
