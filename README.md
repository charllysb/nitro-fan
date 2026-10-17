# Nitro Fan

Liga/desliga o modo turbo (Max Fan) do Acer Nitro sem o NitroSense e mostra
temperatura e RPM de CPU e GPU, com alerta de temperatura crítica.

Testado no **Nitro AN515-43** (Ryzen + GTX 1650), Windows 11.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `nitro_fan_gui.pyw` | Interface (tkinter): botão turbo/auto, cards de CPU/GPU com gráfico, alerta acima de 90 °C e opção de ligar o turbo sozinho |
| `nitro-fan.ps1` | Versão linha de comando: `turbo`, `auto` ou `info` (lista os métodos WMI do firmware) |
| `TURBO.bat` / `AUTO.bat` | Atalhos de duplo clique para o `nitro-fan.ps1` |

Os dois pedem Administrador sozinhos (UAC). A GUI só precisa de Python 3, sem
dependências. O estado (modo e opções) fica em `%APPDATA%\nitro_fan_estado.json`.

## Como funciona

Fala direto com o firmware pela classe WMI `root/WMI:AcerGamingFunction`, a mesma
que o NitroSense usa.

**Modo dos fans:** `SetGamingFanBehavior(gmInput: UInt64)`
- bits 0–15: quais fans (`0x0009` = CPU + GPU)
- bits 16–23: modo de cada fan (`01` = auto, `10` = turbo)
- turbo = `0x820009`, auto = `0x410009`

**Sensores:** `GetGamingSysInfo(gmInput: UInt32)` com `gmInput = 0x0001 | (id << 8)`.
Resposta: bits 0–7 = status (0 = ok), bits 8–23 = valor.

| id | Sensor |
|---|---|
| `0x01` | Temperatura CPU (°C) |
| `0x02` | Fan CPU (RPM) |
| `0x06` | Fan GPU (RPM) |
| `0x0A` | Temperatura GPU (°C) |

Neste modelo `MSAcpi_ThermalZoneTemperature` responde "Sem suporte", então o
firmware é a única fonte de temperatura da CPU.

## Armadilha

`Invoke-CimMethod` devolve um `PSCustomObject` (`gmOutput`, `ReturnValue`,
`PSComputerName`), não um `CimInstance`. Ler `$r.CimInstanceProperties` ou
`$r.OutParameters` dá `$null`, que vira 0, e a interface mostra "--". A saída se lê
por `$r.PSObject.Properties` (ou `$r.gmOutput`).
