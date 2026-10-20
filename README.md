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

- **Três modos:** AUTO (a curva de fábrica do firmware), CURVA (a sua) e TURBO
  (as duas ventoinhas no máximo). Um clique na ventoinha, ou a barra de espaço,
  alterna entre Auto e Turbo.
- **Curva própria:** você escolhe a faixa. As ventoinhas ficam em 40 % até X °C e
  sobem em linha reta até 100 % em Y °C, pela temperatura mais alta entre CPU e GPU.
  Sobem na hora e descem devagar (5 % a cada 2 s), para não ficarem oscilando. Se
  a leitura dos sensores falhar, se a gravação falhar ou ao fechar o app, o
  controle volta ao Auto. Na próxima vez que o app abre, a curva é retomada.
- **Temperatura e RPM** de CPU e GPU, atualizados a cada 2 segundos.
- **Gráfico** dos últimos 5 minutos, com a escala acompanhando os dados.
- **Turbo automático por temperatura (opcional):** você escolhe a faixa. Por
  exemplo, liga o turbo acima de 85 °C e volta ao Auto abaixo de 70 °C. A
  diferença entre os dois evita que fique ligando e desligando. O turbo ligado à
  mão nunca é desligado sozinho, e se você mudar o modo à mão o automático espera
  a temperatura cair antes de agir de novo.
- **Alerta acima de 90 °C:** a janela vem para frente e pisca. Com o turbo
  automático ou a curva ligados, só pisca na barra de tarefas, sem cobrir o que você está fazendo.
- **Fica na bandeja:** ao minimizar, o app vai para perto do relógio. O ícone
  mostra a temperatura mais alta, com a cor mudando conforme o calor. Passando o
  mouse aparecem CPU, GPU, RPM e o modo. O clique direito abre o menu (Abrir,
  Auto/Curva/Turbo, Sair).
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

Com o Python 3 instalado, dê dois cliques em `nitro_fan_gui.pyw`. O ícone na
bandeja precisa de `python -m pip install pystray pillow`; sem eles, o resto
funciona normalmente. Para usar só pela linha de comando, existem os atalhos `TURBO.bat` e
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
- bits 16–23: modo de cada uma, 2 bits por ventoinha (`01` = auto, `10` = turbo,
  `11` = manual)
- turbo = `0x820009`, auto = `0x410009`, manual = `0xC30009`

**Rotação manual:** `SetGamingFanSpeed(gmInput: UInt64)` com `gmInput = id | (% << 8)`,
id `1` = CPU e `4` = GPU. Só vale no modo manual. `GetGamingFanSpeed(id)` devolve a %
gravada. Medido no AN515-43 (o firmware não tem tabela de curva própria:
`GetGamingFanTable` responde status 2):

| % | CPU | GPU |
|---|---|---|
| 20–40 | ~3950 RPM (piso do modo manual) | ~3750 RPM |
| 60 | ~4550 RPM | ~4450 RPM |
| 80 | 5769 RPM | 5660 RPM |
| 100 (= turbo) | 5769 RPM | 5769 RPM |

No Auto o firmware desce até ~2500 RPM, abaixo do piso do modo manual. A curva
serve para resfriar mais cedo, não para ficar mais silenciosa que o Auto.

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
python -m pip install pyinstaller pystray pillow
build.bat
```
