<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Helm Chart for OAI Access and Mobility Function (AMF)

This Helm chart deploys the **OpenAirInterface Access and Mobility Function (OAI-AMF)**, compliant with **3GPP Release 16**.

The chart has been tested on:
- [Minikube](https://minikube.sigs.k8s.io/docs/)
- [Red Hat OpenShift](https://www.redhat.com/en/technologies/cloud-computing/openshift) versions **4.16–4.20**

No special resource requirements are needed for this network function (NF).

---

## ⚠️ Important Notes

- All extra (Multus) interfaces created inside the AMF pod use **`macvlan`** mode.
  If your environment does **not** support `macvlan`, you must modify the Multus network definition accordingly.

- Starting from **OAI 5G Core v2.0.0**,
  - Functional configuration resides in `config.yaml`
  - Infrastructure and deployment parameters (including images) are defined in `values.yaml`

---

## 🧩 Overview

OAI-AMF implements control-plane functions for 5G Core according to 3GPP Rel.16.
More details about its features can be found on the [OAI-AMF Wiki](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-amf/-/wikis/home).

Source code: [GitLab – OAI AMF](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-amf)
Container images: [Docker Hub – oaisoftwarealliance/oai-amf](https://hub.docker.com/r/oaisoftwarealliance/oai-amf)

- `develop` tag → latest development build
- `latest` tag → current stable build
- `vX.Y.Z` tags → official releases
- Only **Ubuntu 22.04** images are published.
  For **Red Hat/UBI images**, build locally using [this guide](../../../openshift/README.md).

---

## 📁 Chart Structure

```
├── Chart.yaml
├── README.md
├── templates/
│ ├── configmap.yaml
│ ├── deployment.yaml
│ ├── _helpers.tpl
│ ├── nad.yaml
│ ├── NOTES.txt
│ ├── rbac.yaml
│ ├── serviceaccount.yaml
│ └── service.yaml
├── config.yaml # AMF configuration (functional parameters)
└── values.yaml # Deployment configuration (infrastructure parameters)
```

The chart creates the following Kubernetes resources:
1. **Service**
2. **Role-Based Access Control (RBAC)**: Role and RoleBinding
3. **Deployment**
4. **ConfigMap** – holds the AMF configuration file
5. **ServiceAccount**
6. **NetworkAttachmentDefinition** (only when Multus is enabled)

---

## ⚙️ Configuration Parameters

All configurable parameters are defined in [`values.yaml`](./values.yaml).  
Below are the main sections:

| Parameter | Allowed Values | Description |
|------------|----------------|-------------|
| **kubernetesDistribution** | `Vanilla` / `Openshift` | Select your Kubernetes flavor |
| **nfimage.repository** | String | Container image name |
| **nfimage.version** | String | Image tag |
| **nfimage.pullPolicy** | `IfNotPresent` / `Always` / `Never` | Kubernetes pull policy |
| **serviceAccount.create** | `true` / `false` | Create a dedicated ServiceAccount |
| **serviceAccount.annotations** | Map | Optional metadata annotations |
| **exposedPorts.sctp** | Integer | SCTP port exposed (default: 38412) |
| **exposedPorts.sbi** | Integer | HTTP SBI port exposed (default: 80) |
| **podSecurityContext.runAsUser** | Integer | Must be set to `0` (root) |
| **podSecurityContext.runAsGroup** | Integer | Must be set to `0` (root) |

---

### 🕸️ Multus Configuration

| Parameter | Type | Description |
|------------|------|-------------|
| **multus.enabled** | `true` / `false` | Enable Multus networking (default: false) |
| **multus.interfaces[].name** | String | Interface name inside the container |
| **multus.interfaces[].hostInterface** | String | Host-side network interface |
| **multus.interfaces[].ipAdd** | IPv4 | Static IP assigned to the interface |
| **multus.interfaces[].netmask** | CIDR or netmask | Network mask |
| **multus.interfaces[].gateway** | IPv4 (optional) | Gateway for that interface |
| **multus.interfaces[].defaultRoute** | IPv4 (optional) | Default route inside the pod |
| **multus.interfaces[].enabled** | `true` / `false` | Enable/disable the interface |

> **Tip:**  
> If Multus is used without a gateway, leave the `gateway` and `defaultRoute` parameters empty or commented out.  
> Incorrect gateway configuration can break pod networking and DNS resolution.

---

### 🧰 Debugging & Developer Options

| Parameter | Values | Description |
|------------|---------|-------------|
| **start.amf** | `true` / `false` | If `false`, AMF container sleeps (manual debugging) |
| **start.tcpdump** | `true` / `false` | If `true`, tcpdump sidecar starts in sleep mode |
| **includeTcpDumpContainer** | `true` / `false` | Add tcpdump sidecar for packet capture |
| **tcpdumpimage.repository** | String | Tcpdump sidecar image name |
| **tcpdumpimage.version** | String | Tcpdump sidecar tag |
| **tcpdumpimage.pullPolicy** | String | Pull policy |
| **persistent.sharedvolume** | `true` / `false` | Store PCAPs in a shared PVC (created with NRF) |
| **resources.define** | `true` / `false` | Enable resource limits/requests |
| **resources.limits.nf.cpu / memory** | String | CPU/memory limits for AMF |
| **resources.limits.tcpdump.cpu / memory** | String | CPU/memory limits for tcpdump |
| **readinessProbe** | `true` / `false` | Enable readiness probes (default: true) |
| **livenessProbe** | `true` / `false` | Enable liveness probes (default: false) |
| **terminationGracePeriodSeconds** | Integer | Grace period before force shutdown (default: 5s) |
| **nodeSelector / nodeName** | String / Map | Node scheduling constraints |

---

## 🚀 Installation

It is recommended to deploy AMF using one of the **parent OAI Helm charts**, which orchestrate multiple 5G Core NFs together:

1. [`oai-5g-basic`](../oai-5g-basic/README.md) – Minimal core deployment  
2. [`oai-5g-mini`](../oai-5g-mini/README.md) – AMF + SMF + NRF + UPF (AMF acts as AUSF + UDR)  
3. [`oai-5g-slicing`](../oai-5g-slicing/README.md) – Includes NSSF for network slicing  

### Standalone Installation

```bash
helm install oai-amf ./oai-amf -n oai --create-namespace
```

To upgrade or update the configuration:

```
helm upgrade oai-amf ./oai-amf -n oai -f custom-values.yaml
```
To uninstall:

```
helm uninstall oai-amf -n oai
```

---

## 📝 Notes & Recommendations

1. **Multus users**: Ensure Multus CNI is properly configured before enabling it in the chart. Avoid setting incorrect gateway values to prevent pod networking issues.

2. **Tcpdump sidecar**: If `start.tcpdump: true` is enabled then also enable `persistent.sharedvolume: true` in both AMF and NRF charts
to collect PCAPs in a shared volume for centralized analysis.

3. **Resource Tuning**: Default resource settings are conservative. Adjust CPU/memory requests and limits according to your cluster’s available resources.

---

## References

1. [OAI-AMF Source Code](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-amf)
2. [OAI Docker Hub Images](https://hub.docker.com/repository/docker/oaisoftwarealliance/oai-amf)
4. [Kubernetes Resource Management Docs](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
