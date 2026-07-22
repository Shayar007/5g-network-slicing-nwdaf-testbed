# Helm Chart for OAI Location Management Function (LMF)

This Helm chart deploys the **OpenAirInterface Location Management Function (OAI-LMF)**, compliant with **3GPP Release 16**.

The chart has been tested on:
- [Minikube](https://minikube.sigs.k8s.io/docs/)
- [Red Hat OpenShift](https://www.redhat.com/en/technologies/cloud-computing/openshift) versions **4.16–4.20**

No special resource requirements are needed for this network function (NF).

---

## ⚠️ Important Notes
  
- Starting from **OAI 5G Core v2.0.0**,  
  - Functional configuration resides in `config.yaml`
  - Infrastructure and deployment parameters (including images) are defined in `values.yaml`

---

## 🧩 Overview

OAI-LMF implements control-plane functions for 5G Core according to 3GPP Rel.16.  
More details about its features can be found on the [OAI-LMF Wiki](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-lmf/-/wikis/home).

Source code: [GitLab – OAI LMF](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-lmf)  
Container images: [Docker Hub – oaisoftwarealliance/oai-lmf](https://hub.docker.com/r/oaisoftwarealliance/oai-lmf)

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
│ ├── NOTES.txt
│ ├── rbac.yaml
│ ├── serviceaccount.yaml
│ └── service.yaml
├── config.yaml # LMF configuration (functional parameters)
└── values.yaml # Deployment configuration (infrastructure parameters)
```

The chart creates the following Kubernetes resources:
1. **Service**
2. **Role-Based Access Control (RBAC)**: Role and RoleBinding
3. **Deployment**
4. **ConfigMap** – holds the LMF configuration file
5. **ServiceAccount**

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
| **exposedPorts.sbi** | Integer | HTTP SBI port exposed (default: 80) |
| **podSecurityContext.runAsUser** | Integer | Must be set to `0` (root) |
| **podSecurityContext.runAsGroup** | Integer | Must be set to `0` (root) |

---

### 🧰 Debugging & Developer Options

| Parameter | Values | Description |
|------------|---------|-------------|
| **start.lmf** | `true` / `false` | If `false`, LMF container sleeps (manual debugging) |
| **start.tcpdump** | `true` / `false` | If `true`, tcpdump sidecar starts in sleep mode |
| **includeTcpDumpContainer** | `true` / `false` | Add tcpdump sidecar for packet capture |
| **tcpdumpimage.repository** | String | Tcpdump sidecar image name |
| **tcpdumpimage.version** | String | Tcpdump sidecar tag |
| **tcpdumpimage.pullPolicy** | String | Pull policy |
| **persistent.sharedvolume** | `true` / `false` | Store PCAPs in a shared PVC (created with NRF) |
| **resources.define** | `true` / `false` | Enable resource limits/requests |
| **resources.limits.nf.cpu / memory** | String | CPU/memory limits for LMF |
| **resources.limits.tcpdump.cpu / memory** | String | CPU/memory limits for tcpdump |
| **readinessProbe** | `true` / `false` | Enable readiness probes (default: true) |
| **livenessProbe** | `true` / `false` | Enable liveness probes (default: false) |
| **terminationGracePeriodSeconds** | Integer | Grace period before force shutdown (default: 5s) |
| **nodeSelector / nodeName** | String / Map | Node scheduling constraints |

---

## 🚀 Installation

It is recommended to deploy LMF using one of the **parent OAI Helm charts**, which orchestrate multiple 5G Core NFs together:

1. [`oai-5g-basic`](../oai-5g-basic/README.md) – Minimal core deployment  
3. [`oai-5g-slicing`](../oai-5g-slicing/README.md) – Includes NSSF for network slicing  

### Standalone Installation

```bash
helm install oai-lmf ./oai-lmf -n oai --create-namespace
```

To upgrade or update the configuration:

```
helm upgrade oai-lmf ./oai-lmf -n oai -f custom-values.yaml
```
To uninstall:

```
helm uninstall oai-lmf -n oai
```

---

## 📝 Notes & Recommendations

1. **Tcpdump sidecar**: If `start.tcpdump: true` is enabled then also enable `persistent.sharedvolume: true` in both LMF and NRF charts
to collect PCAPs in a shared volume for centralized analysis.

2. **Resource Tuning**: Default resource settings are conservative. Adjust CPU/memory requests and limits according to your cluster’s available resources.

---

## References

1. [OAI-LMF Source Code](https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-lmf)
2. [OAI Docker Hub Images](https://hub.docker.com/repository/docker/oaisoftwarealliance/oai-lmf)
4. [Kubernetes Resource Management Docs](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
