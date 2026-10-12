"""LLM tool definitions and risk classification."""

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "nodes_list",
            "description": "List all nodes in the Proxmox cluster and their status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "node_status",
            "description": "Get detailed status, CPU/RAM utilization, and hardware specs for a specific node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target Proxmox node name (e.g. pveheavy, pvestore)."}
                },
                "required": ["node"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_vmid",
            "description": "Locate a VM or LXC container by its numeric VMID across all cluster nodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid": {"type": "integer", "description": "VM or Container numeric ID (e.g. 100)."},
                    "kind": {"type": "string", "enum": ["vm", "lxc"], "description": "Expected resource type."}
                },
                "required": ["vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vms_list",
            "description": "List QEMU/KVM virtual machines on a specific node or across the entire cluster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Filter VMs by specific node (optional). Omit to list across all nodes."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vms_next_id",
            "description": "Get the next available unique numeric VM/LXC ID from the Proxmox cluster.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxcs_list",
            "description": "List LXC containers on a specific node or across the entire cluster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Filter LXCs by specific node (optional). Omit to list across all nodes."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxcs_next_id",
            "description": "Get the next available unique numeric VM/LXC ID from the Proxmox cluster.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_status",
            "description": "Get runtime status, memory, CPU usage, and uptime for a VM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_config",
            "description": "Retrieve configuration parameters (cores, memory, disks, network) of a VM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_guest_network",
            "description": "Query IP addresses and network interfaces of a VM using the QEMU guest agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_exec",
            "description": "Execute a shell command directly inside a running VM using the Proxmox QEMU Guest Agent REST API. Requires QEMU guest agent installed and running in the VM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."},
                    "command": {"type": "string", "description": "Shell command to execute inside the VM (e.g. 'uptime', 'ip a', 'cat /etc/os-release', 'systemctl status docker')."},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds (default: 30)."}
                },
                "required": ["node", "vmid", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_create",
            "description": "Create a new QEMU/KVM virtual machine. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target node (e.g. pveheavy)."},
                    "vmid": {"type": "integer", "description": "Numeric VM ID (use vms_next_id if unknown)."},
                    "name": {"type": "string", "description": "Virtual machine name."},
                    "memory": {"type": "integer", "description": "Memory in MB (e.g. 2048)."},
                    "cores": {"type": "integer", "description": "Number of CPU cores."},
                    "storage": {"type": "string", "description": "Storage pool (e.g. local-lvm, local-zfs, SSDStore)."},
                    "net0": {"type": "string", "description": "Network config (e.g. virtio,bridge=vmbr0)."},
                    "ostype": {"type": "string", "description": "OS type (e.g. l26, win11)."}
                },
                "required": ["node", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_start",
            "description": "Start a virtual machine. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID to start."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_shutdown",
            "description": "Gracefully shutdown a virtual machine via ACPI. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID to shutdown."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_reboot",
            "description": "Reboot a virtual machine. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID to reboot."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_stop",
            "description": "Forcefully power off a VM (hard kill). Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID to forcefully stop."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_delete",
            "description": "Delete a virtual machine and its virtual disks. Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID to delete."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_snapshot_create",
            "description": "Create a snapshot of a virtual machine. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."},
                    "name": {"type": "string", "description": "Snapshot name."}
                },
                "required": ["node", "vmid", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_snapshot_delete",
            "description": "Delete a VM snapshot. Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the VM is hosted."},
                    "vmid": {"type": "integer", "description": "VM ID."},
                    "name": {"type": "string", "description": "Snapshot name."}
                },
                "required": ["node", "vmid", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_status",
            "description": "Get runtime status, memory, CPU, and disk usage for an LXC container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the LXC is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_config",
            "description": "Retrieve configuration parameters for an LXC container. READ-ONLY: this cannot change anything — to set a static IP, hostname, or DNS, use lxc_set_config.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the LXC is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_set_config",
            "description": "Update an LXC container's configuration, e.g. set a static IP via net0. Read the current config with lxc_config first and preserve bridge/firewall/type/hwaddr when rewriting net0 (only replace ip=dhcp with ip=<addr>/<cidr>). The container should be stopped. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the LXC is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."},
                    "net0": {"type": "string", "description": "Full network interface string (e.g. name=eth0,bridge=vmbr0,firewall=1,ip=192.168.1.79/24,gw=192.168.1.1,type=veth)."},
                    "hostname": {"type": "string", "description": "Container hostname."},
                    "nameserver": {"type": "string", "description": "DNS server(s)."},
                    "searchdomain": {"type": "string", "description": "DNS search domain."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_create",
            "description": "Create a new LXC container from an OS tarball. A root password is REQUIRED and the harness securely prompts for it before execution. Do not ask the user to type a password into chat and never invent or assume one. An SSH public key is optional but strongly recommended for reliable access. This requirement does not apply to lxc_clone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target node (e.g. pveheavy)."},
                    "vmid": {"type": "integer", "description": "Numeric CT ID (use lxcs_next_id if unknown)."},
                    "hostname": {"type": "string", "description": "Container hostname."},
                    "ostemplate": {"type": "string", "description": "OS template (e.g. local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst)."},
                    "storage": {"type": "string", "description": "Storage pool for rootfs (e.g. local-lvm, local-zfs, SSDStore)."},
                    "memory": {"type": "integer", "description": "Memory in MB (e.g. 1024)."},
                    "cores": {"type": "integer", "description": "Number of CPU cores."},
                    "net0": {"type": "string", "description": "Network configuration (e.g. name=eth0,bridge=vmbr0,ip=dhcp)."},
                    "unprivileged": {"type": "boolean", "description": "Create unprivileged container (default true)."},
                    "ssh_keygen": {"type": "boolean", "description": "Optional but strongly recommended: generate an SSH key pair and inject its public key for reliable SSH access."}
                },
                "required": ["node", "hostname"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_start",
            "description": "Start an LXC container. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID to start."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_shutdown",
            "description": "Gracefully shutdown an LXC container. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID to shutdown."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_reboot",
            "description": "Reboot an LXC container. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID to reboot."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_stop",
            "description": "Forcefully stop an LXC container. Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID to stop."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_delete",
            "description": "Delete an LXC container and its disk storage. Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID to delete."}
                },
                "required": ["node", "vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_snapshot_create",
            "description": "Create a snapshot of an LXC container. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."},
                    "name": {"type": "string", "description": "Snapshot name."}
                },
                "required": ["node", "vmid", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_snapshot_delete",
            "description": "Delete an LXC snapshot. Destructive action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."},
                    "name": {"type": "string", "description": "Snapshot name."}
                },
                "required": ["node", "vmid", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_exec",
            "description": "Execute a shell command directly inside a running LXC container via Proxmox API or SSH fallback. If the console needs login, OMIT password/user — the harness securely prompts the operator via getpass. NEVER ask the user to type a password into chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node where the container is hosted."},
                    "vmid": {"type": "integer", "description": "LXC container ID."},
                    "command": {"type": "string", "description": "Shell command to execute inside the container (e.g. 'apt update', 'systemctl status nginx', 'cat /etc/os-release')."},
                    "user": {"type": "string", "description": "Username for console or SSH login (default: root)."},
                    "password": {"type": "string", "description": "Optional password for console login if required."},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds (default: 30)."}
                },
                "required": ["node", "vmid", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_pct_exec",
            "description": (
                "Run a shell command inside a running LXC container using the Proxmox node's "
                "`pct exec`, over SSH to the node. This is the reliable out-of-band path: it needs "
                "no login prompt, no console, and no guest networking, so prefer it when guest SSH "
                "is unavailable. Supply only the container ID and the command. The node, the `pct` "
                "invocation and the shell are chosen by the harness and cannot be overridden; "
                "commands that are not exactly what the harness assembles are rejected. Prompts the "
                "user for approval. Do not use this to reach the Proxmox node itself - use the API "
                "tools for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node hosting the container. Defaults to the configured node."},
                    "vmid": {"type": "integer", "description": "LXC container ID."},
                    "command": {"type": "string", "description": "Shell command to run inside the container (e.g. 'systemctl status nginx', 'apt update')."},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds (default: 30)."}
                },
                "required": ["vmid", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": "Execute a shell command over SSH on a VM, LXC container, or remote host. Prompts user for approval. On authentication failure the harness stops and asks the operator for a corrected user/key-file/password instead of retrying — do NOT retry the same credentials.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute over SSH."},
                    "host": {"type": "string", "description": "Target hostname or IP address (optional if node and vmid are supplied)."},
                    "node": {"type": "string", "description": "Proxmox node name (used to resolve guest IP if host not provided)."},
                    "vmid": {"type": "integer", "description": "Target VM or LXC ID (used to resolve guest IP if host not provided)."},
                    "kind": {"type": "string", "enum": ["vm", "lxc"], "description": "Target resource type for IP resolution."},
                    "user": {"type": "string", "description": "SSH username (default: root)."},
                    "port": {"type": "integer", "description": "SSH port (default: 22)."},
                    "key_file": {"type": "string", "description": "Path to SSH private key file (optional)."},
                    "password": {"type": "string", "description": "SSH password (optional; prefer key auth. The harness prompts securely on auth failure — never ask the user to paste it into chat)."},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds (default: 30)."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "storage_list",
            "description": "List storage pools, types, capacity, and available free space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target node name (optional)."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "storage_content",
            "description": "List volumes, ISOs, and container templates stored in a storage pool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "storage": {"type": "string", "description": "Storage pool name (e.g. local, local-lvm, SSDStore)."},
                    "node": {"type": "string", "description": "Target node name (optional)."}
                },
                "required": ["storage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "storage_templates",
            "description": "List available LXC container templates on a storage pool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "storage": {"type": "string", "description": "Storage name containing templates (e.g. local)."},
                    "node": {"type": "string", "description": "Target node name (optional)."}
                },
                "required": ["storage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "storage_download",
            "description": "Download an LXC OS template from a URL into storage. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "storage": {"type": "string", "description": "Target storage (e.g. local)."},
                    "url": {"type": "string", "description": "Template download URL."},
                    "filename": {"type": "string", "description": "Target filename."},
                    "node": {"type": "string", "description": "Target node name (optional)."}
                },
                "required": ["storage", "url", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tasks_recent",
            "description": "List recent background tasks executed on Proxmox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target node name (optional)."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "doctor",
            "description": "Check Proxmox API connectivity, configuration settings, and health.",
            "parameters": {
                "type": "object",
                "properties": {
                    "live": {"type": "boolean", "description": "Perform live API ping test."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "health",
            "description": "Show overall Proxmox cluster health summary and resource status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "templates_list",
            "description": "List all VM templates (QEMU VMs converted to templates), LXC container templates, and storage OS tarball templates across the cluster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["all", "vm", "lxc", "os_template"],
                        "description": "Filter by template kind (default: 'all')."
                    },
                    "node": {
                        "type": "string",
                        "description": "Filter by node (optional)."
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cluster_summary",
            "description": "Get a fresh compact overview of the cluster: nodes, storage pools with content types, network bridges, LXC/VM placement, and the next free VMID. Read-only. Prefer this over separate nodes_list/storage_list/lxcs_list calls at the start of a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Limit the summary to one node (optional)."
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "templates_registry",
            "description": "Browse the upstream LXC template registry (pveam available). Use this when the user wants to find or download a container template that is not already cached on local storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Proxmox node to query the registry from (optional, defaults to first online node)."
                    },
                    "section": {
                        "type": "string",
                        "enum": ["system", "turnkeylinux", "mail"],
                        "description": "Filter by registry section (optional)."
                    },
                    "search": {
                        "type": "string",
                        "description": "Substring filter over template, package, headline, description, os (optional)."
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "templates_download",
            "description": "Download an upstream registry template by name into storage (pveam download). Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Target Proxmox node name."
                    },
                    "storage": {
                        "type": "string",
                        "description": "Storage where the template will be stored (e.g. local)."
                    },
                    "template": {
                        "type": "string",
                        "description": "Registry template name from templates_registry (e.g. debian-13-standard_13.6-1_amd64.tar.zst)."
                    }
                },
                "required": ["node", "storage", "template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lxc_clone",
            "description": "Clone an LXC container or converted LXC template (template:1, e.g. vmid 20300) into a new container. Use this — never lxc_create — when the source is a template VMID. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node hosting the source template (e.g. pveheavy)."},
                    "vmid": {"type": "integer", "description": "Source template/container VMID (e.g. 20300)."},
                    "new_vmid": {"type": "integer", "description": "New container ID (use lxcs_next_id if unknown)."},
                    "hostname": {"type": "string", "description": "Hostname for the clone (e.g. ansible-controller)."},
                    "storage": {"type": "string", "description": "Target storage pool for the clone rootfs (e.g. SSDStore, local-zfs)."},
                    "pool": {"type": "string", "description": "Target pool (optional)."},
                    "snapname": {"type": "string", "description": "Snapshot to clone from (optional)."}
                },
                "required": ["node", "vmid", "new_vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_clone",
            "description": "Clone a QEMU VM or VM template into a new VM. Use this — never vm_create — when the source is a template VMID. Prompts user for approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node hosting the source template."},
                    "vmid": {"type": "integer", "description": "Source VM/template VMID."},
                    "new_vmid": {"type": "integer", "description": "New VM ID (use vms_next_id if unknown)."},
                    "name": {"type": "string", "description": "Name for the clone."},
                    "storage": {"type": "string", "description": "Target storage pool for full clone."},
                    "full": {"type": "boolean", "description": "Full clone (default: Proxmox default)."}
                },
                "required": ["node", "vmid", "new_vmid"],
            },
        },
    },
]

# Classification of risk levels
READ_ONLY_TOOLS = {
    "nodes_list", "node_status", "resolve_vmid", "vms_list", "vms_next_id",
    "lxcs_list", "lxcs_next_id", "templates_list", "templates_registry", "cluster_summary", "vm_status", "vm_config", "vm_guest_network",
    "lxc_status", "lxc_config", "storage_list", "storage_content",
    "storage_templates", "tasks_recent", "doctor", "health"
}
WRITE_TOOLS = {
    "vm_create", "lxc_create", "vm_clone", "lxc_clone", "lxc_set_config", "vm_start", "vm_shutdown", "vm_reboot",
    "vm_snapshot_create", "lxc_start", "lxc_shutdown", "lxc_reboot",
    "lxc_snapshot_create", "storage_download", "templates_download", "vm_exec", "lxc_exec", "ssh_exec",
    "lxc_pct_exec",
}
DESTRUCTIVE_TOOLS = {
    "vm_stop", "lxc_stop", "vm_delete", "lxc_delete",
    "vm_snapshot_delete", "lxc_snapshot_delete"
}

# Tools whose command is assembled and validated by the harness, not taken from
# the model. `agent.py` refuses these before the approval prompt, so typing `y`
# cannot wave through a command that is not exactly what the harness would build.
HARNESS_ASSEMBLED_TOOLS = {"lxc_pct_exec"}
