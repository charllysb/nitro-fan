# Nitro Fan

**Alternativa leve ao NitroSense para notebooks Acer Nitro no Windows 11.**
Liga o modo turbo (Max Fan) e mostra temperatura e rotação de CPU e GPU, sem
instalar o NitroSense nem o Acer Control Center.

> *English:* lightweight NitroSense alternative for Acer Nitro laptops on Windows 11.
> Toggles Max Fan (turbo) and shows CPU/GPU temperature and fan RPM by talking
> directly to the Acer firmware over WMI. Single `.exe`, no install.

## Por que existe

O NitroSense é pesado, depende de serviços da Acer e, em muitos Nitro, para de
funcionar ou some depois de atualizar para o Windows 11 ou de uma reinstalação
limpa. Mas o controle das ventoinhas não está no NitroSense: está no firmware do
notebook, exposto pelo Windows como a classe WMI `AcerGamingFunction`. O Nitro Fan
chama esse mesmo firmware diretamente. Funciona sem driver extra, sem serviço
rodando em segundo plano e sem nada instalado.

## O que faz

- **Turbo / Auto:** um clique na ventoinha (ou a barra de espaço) alterna entre o
  modo turbo, com CPU e GPU no máximo, e o controle automático do firmware.
- **Temperatura e RPM** de CPU e GPU, atualizados a cada 2 segundos.
- **Gráfico** dos últimos 5 minutos, com a escala acompanhando os dados.
- **Alerta acima de 90 °C:** a janela vem para frente e pisca. Opcionalmente,
  liga o turbo sozinho.
- **Volta ao Auto ao fechar** (opcional), para o notebook não ficar no turbo
  depois de fechado.

## Como usar

1. Baixe o `NitroFan.exe` em [Releases](../../releases).
2. Execute o arquivo. Ele pede permissão de Administrador, porque o Windows só
   deixa um programa falar com o firmware com essa permissão.

Não precisa instalar nada. As opções ficam salvas em `%APPDATA%\nitro_fan_estado.json`.

**Avisos que podem aparecer:**
- O SmartScreen pode mostrar "O Windows protegeu o computador", porque o executável
  não é assinado. Clique em *Mais informações* → *Executar assim mesmo*.
- Alguns antivírus estranham executáveis gerados pelo PyInstaller (falso
  positivo). Se preferir, rode direto do código-fonte (veja abaixo).

### Sem o executável

Com o Python 3 instalado, dê dois cliques em `nitro_fan_gui.pyw`. Não há
dependências. Para usar só pela linha de comando, existem os atalhos `TURBO.bat` e
`AUTO.bat` (que chamam o `nitro-fan.ps1`).

## Compatibilidade

Testado no **Acer Nitro 5 AN515-43** (Ryzen 5 3550H + GTX 1650) com Windows 11.

Deve funcionar em outros Nitro e Predator que expõem a classe `AcerGamingFunction`.
Para saber se o seu modelo expõe, rode como Administrador:

```powershell
.\nitro-fan.ps1 info
```

Se aparecer a lista de métodos com `SetGamingFanBehavior` e `GetGamingSysInfo`, o
modelo provavelmente é compatível. Se ele não tiver a classe, o programa avisa e não
muda nada. Funcionou (ou não) no seu modelo? Abra uma issue com o nome do modelo.

> Projeto independente, sem ligação com a Acer. Ele usa a mesma interface do
> firmware que o NitroSense usa e só alterna entre os dois modos de fábrica (Auto
> e Max), sem curva de ventoinha customizada. Use por sua conta.

## Como funciona

Classe WMI `root/WMI:AcerGamingFunction`.

**Modo das ventoinhas:** `SetGamingFanBehavior(gmInput: UInt64)`
- bits 0–15: quais ventoinhas (`0x0009` = CPU + GPU)
- bits 16–23: modo de cada uma (`01` = auto, `10` = turbo)
- turbo = `0x820009`, auto = `0x410009`

**Sensores:** `GetGamingSysInfo(gmInput: UInt32)` com `gmInput = 0x0001 | (id << 8)`.
Resposta: bits 0–7 = status (0 = ok), bits 8–23 = valor.

| id | Sensor |
|---|---|
| `0x01` | Temperatura da CPU (°C) |
| `0x02` | Ventoinha da CPU (RPM) |
| `0x06` | Ventoinha da GPU (RPM) |
| `0x0A` | Temperatura da GPU (°C) |

No AN515-43, `MSAcpi_ThermalZoneTemperature` responde "Sem suporte", então o
firmware é a única fonte de temperatura da CPU.

**Armadilha:** `Invoke-CimMethod` devolve um `PSCustomObject` (`gmOutput`,
`ReturnValue`, `PSComputerName`), não um `CimInstance`. Ler `$r.CimInstanceProperties`
ou `$r.OutParameters` dá `$null`, que vira 0. A saída se lê por
`$r.PSObject.Properties` (ou `$r.gmOutput`).

## Arquivos

| Arquivo | O que é |
|---|---|
| `nitro_fan_gui.pyw` | A interface (Python + tkinter) |
| `nitro-fan.ps1` | Versão linha de comando: `turbo`, `auto` ou `info` |
| `TURBO.bat` / `AUTO.bat` | Atalhos de duplo clique para o `nitro-fan.ps1` |
| `build.bat` | Gera o `dist\NitroFan.exe` com o PyInstaller |

Para gerar o executável:

```bat
python -m pip install pyinstaller
build.bat
```
