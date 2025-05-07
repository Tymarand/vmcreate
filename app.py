from flask import Flask, render_template, request, redirect, flash
from proxmoxer import ProxmoxAPI
from dotenv import load_dotenv
import os
import time

load_dotenv()

app = Flask("Proxmox VM Creation Form")
app.secret_key = 'your_secret_key'  # Replace with a secure value in production

# Connect to Proxmox
def connect_proxmox():
    return ProxmoxAPI(
        os.getenv('PVE_HOST'),
        user=os.getenv('PVE_USER'),
        password=os.getenv('PVE_PASSWORD'),
        verify_ssl=False
    )

@app.route('/', methods=['GET', 'POST'])
def index():
    proxmox = connect_proxmox()
    node = os.getenv('PVE_NODE')

    # Get list of templates
    templates = []
    for vm in proxmox.nodes(node).qemu.get():
        if vm.get('template') == 1:
            templates.append({'vmid': vm['vmid'], 'name': vm['name']})

    # Get next available VM ID
    nextid = proxmox.cluster.nextid.get()

    if request.method == 'POST':
        action = request.form['action']
        vmid = int(request.form['vmid'])
        name = request.form['name']
        cpu = request.form['cpu']
        ram = int(request.form['ram']) * 1024  # Convert GB to MB
        storage = request.form['storage']
        bridge = request.form['bridge']
        iso = request.form.get('iso')
        template_id = request.form.get('template')
        power_on = request.form.get('power_on') == 'on'

        try:
            if action == 'clone' and template_id:
                # Clone the template and wait for it to complete
                clone_task = proxmox.nodes(node).qemu(template_id).clone.post(
                    newid=vmid,
                    name=name,
                    target=node,
                    full=1,
                    storage=storage
                )

                # Extract UPID from the clone task
                upid = clone_task

                # Wait for the task to complete
                while True:
                    task_status = proxmox.nodes(node).tasks(upid).status.get()
                    if task_status.get('status') == 'stopped':
                        if task_status.get('exitstatus') == 'OK':
                            break
                        else:
                            raise Exception(f"Clone failed: {task_status.get('exitstatus')}")
                    time.sleep(2)

                # Apply VM configuration
                proxmox.nodes(node).qemu(vmid).config.post(
                    cores=cpu,
                    memory=ram,
                    net0=f"virtio,bridge={bridge}"
                )

                # Optionally power on
                if power_on:
                    proxmox.nodes(node).qemu(vmid).status.start.post()

                flash(f"Cloned template {template_id} to new VM {vmid}", "success")

            elif action == 'create' and iso:
                proxmox.nodes(node).qemu.post(
                    vmid=vmid,
                    name=name,
                    memory=ram,
                    cores=cpu,
                    net0=f"virtio,bridge={bridge}",
                    ide2=f"{iso},media=cdrom",
                    sata0=f"{storage}:32",  # Default 32GB
                    ostype='l26',
                    boot='order=ide2;net0;sata0'
                )
                if power_on:
                    proxmox.nodes(node).qemu(vmid).status.start.post()
                flash(f"Created new VM {vmid} with ISO install", "success")
        except Exception as e:
            flash(f"Error: {str(e)}", "error")

        return redirect('/')

    # Load full ISO volids
    iso_storage = 'local'  # or change as appropriate
    iso_files = []
    content = proxmox.nodes(node).storage(iso_storage).content.get()
    for item in content:
        if item['content'] == 'iso':
            iso_files.append(item['volid'])  # Keep full volid (e.g. local:iso/file.iso)

    return render_template('index.html',
                           nextid=nextid,
                           templates=templates,
                           iso_files=iso_files,
                           storages=['fastStore', 'dataStore', 'local', 'local-zfs'],
                           cpus=['1', '2', '4', '8'],
                           rams=['1', '2', '4', '8', '16'],
                           bridge='vmbr0')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

