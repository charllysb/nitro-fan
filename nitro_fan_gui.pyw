"""
Nitro Fan - liga/desliga o modo turbo (Max Fan) do Acer Nitro sem o NitroSense,
e mostra temperatura/RPM de CPU e GPU com alerta de temperatura critica.

Fala direto com o firmware via WMI (classe AcerGamingFunction):
  - SetGamingFanBehavior  -> modo dos fans
  - GetGamingSysInfo      -> sensores (comando 0x0001 | id_sensor << 8)
Sem dependencias: so Python 3 (tkinter vem junto). Extensao .pyw = abre sem console.
"""
import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque

try:  # opcional: sem pystray/Pillow o app funciona, so nao vai para a bandeja
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    pystray = None

# ---------------------------------------------------------------- config
VALORES = {"turbo": 0x820009, "auto": 0x410009, "curva": 0xC30009}  # CPU+GPU: 10=turbo, 01=auto, 11=manual
FAN_IDS = (1, 4)    # SetGamingFanSpeed: 1 = CPU, 4 = GPU
ESTADO_ARQ = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "nitro_fan_estado.json")

TEMP_ALERTA = 80    # °C - amarelo
TEMP_CRITICA = 90   # °C - vermelho + alerta
INTERVALO = 2       # segundos entre leituras
HIST_MIN = 5        # minutos no grafico
CURVA_MIN, CURVA_MAX = 40, 100   # % - abaixo de 40% o firmware nao desce do piso (~3900 RPM)
CURVA_PASSO = 5     # % - arredondamento e descida maxima por leitura
SEM_LEITURA_MAX = 10  # s sem leitura no modo curva -> volta ao AUTO

COR_FUNDO = "#0f1012"
COR_CARD = "#1a1c20"
COR_TEXTO = "#e8e8e8"
COR_SUAVE = "#7a7f87"
COR_TURBO = "#e3242b"   # vermelho Nitro
COR_AUTO = "#3a3f47"
COR_OK = "#3ecf6e"
COR_WARN = "#f0a500"
COR_CPU = "#4ea8ff"    # linhas do grafico
COR_GPU = "#c77dff"
COR_CURVA = "#4ea8ff"

NO_WINDOW = 0x08000000

PS_SET = r"""
$ErrorActionPreference='Stop'
$c=Get-CimClass -Namespace root/WMI -ClassName AcerGamingFunction
$i=Get-CimInstance -Namespace root/WMI -ClassName AcerGamingFunction | Select-Object -First 1
$m=$c.CimClassMethods['__METODO__']
$p=($m.Parameters | Where-Object { $_.Qualifiers.Name -contains 'In' } | Select-Object -First 1).Name
if(-not $p){ $p=($m.Parameters | Select-Object -First 1).Name }
foreach($v in @(__VALORES__)){
  $r=Invoke-CimMethod -InputObject $i -MethodName __METODO__ -Arguments @{ $p=[uint64]$v }
  $o=($r.PSObject.Properties | Where-Object { $_.Name -notin 'ReturnValue','PSComputerName' -and $_.Value -ne $null } | Select-Object -First 1).Value
  if($o -ne $null -and ([uint64]$o -band 0xFF) -ne 0){ Write-Output $o; exit }
}
Write-Output 'OK'
"""

# Processo PowerShell persistente: le sensores e imprime 1 linha JSON a cada INTERVALO.
PS_MONITOR = r"""
$ErrorActionPreference='Stop'
try {
  $c=Get-CimClass -Namespace root/WMI -ClassName AcerGamingFunction
  $i=Get-CimInstance -Namespace root/WMI -ClassName AcerGamingFunction | Select-Object -First 1
  $m=$c.CimClassMethods | Where-Object { $_.Name -like 'Get*SysInfo*' } | Select-Object -First 1
  $par=$m.Parameters | Where-Object { $_.Qualifiers.Name -contains 'In' } | Select-Object -First 1
  if(-not $par){ $par=$m.Parameters | Select-Object -First 1 }
  $p=$par.Name; $tipo=[string]$par.CimType
} catch { $m=$null }

function Ler([int]$id){
  if(-not $m){ return -1 }
  try {
    $cmd = 1 -bor ($id -shl 8)
    if($tipo -eq 'UInt32'){ $arg=[uint32]$cmd } else { $arg=[uint64]$cmd }
    $r=Invoke-CimMethod -InputObject $i -MethodName $m.Name -Arguments @{ $p=$arg }
    $o=[uint64](($r.PSObject.Properties | Where-Object { $_.Name -notin 'ReturnValue','PSComputerName' -and $_.Value -ne $null } | Select-Object -First 1).Value)
    if(($o -band 0xFF) -ne 0){ return -1 }
    return [int](($o -shr 8) -band 0xFFFF)
  } catch { return -1 }
}

function TempAcpi {
  try {
    $z=Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
       Sort-Object CurrentTemperature -Descending | Select-Object -First 1
    return [int]($z.CurrentTemperature/10 - 273.15)
  } catch { return -1 }
}

while($true){
  $cpu=Ler 1; if($cpu -le 0 -or $cpu -gt 125){ $cpu=TempAcpi }
  $d=[ordered]@{ cpu=$cpu; gpu=(Ler 10); fcpu=(Ler 2); fgpu=(Ler 6) }
  [Console]::Out.WriteLine(($d | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
  Start-Sleep -Seconds __INTERVALO__
}
"""


# ---------------------------------------------------------------- sistema
def eh_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevar():
    """Reabre o script como Administrador (pythonw para nao abrir console)."""
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{os.path.abspath(__file__)}"', None, 1)
    sys.exit(0)


def ps_args(script):
    """Grava o script num .ps1 e roda com -File. (-EncodedCommand faz antivirus apagar o .exe.)"""
    pasta = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NitroFan")
    os.makedirs(pasta, exist_ok=True)
    arq = os.path.join(pasta, hashlib.sha1(script.encode()).hexdigest()[:12] + ".ps1")
    with open(arq, "w", encoding="utf-8-sig") as f:
        f.write(script)
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", arq]


def chamar_firmware(metodo, valores):
    """Chama um metodo Set* com cada valor, em ordem. Retorna (ok, mensagem)."""
    script = PS_SET.replace("__METODO__", metodo).replace("__VALORES__", ",".join(hex(v) for v in valores))
    try:
        r = subprocess.run(ps_args(script),
                           capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except Exception as e:
        return False, f"Falha ao chamar PowerShell: {e}"
    if r.returncode != 0:
        erro = (r.stderr or r.stdout).strip().splitlines()
        return False, erro[0][:120] if erro else "Erro desconhecido"
    saida = r.stdout.strip()
    if saida.isdigit() and int(saida) & 0xFF != 0:
        return False, f"Firmware recusou (status {int(saida) & 0xFF})"
    return True, "OK"


def aplicar_modo(modo):
    return chamar_firmware("SetGamingFanBehavior", [VALORES[modo]])


def aplicar_rotacao(pct):
    """Rotacao das duas ventoinhas em % (so vale no modo curva)."""
    return chamar_firmware("SetGamingFanSpeed", [fan | (pct << 8) for fan in FAN_IDS])


def pct_da_curva(temp, t_min, t_max):
    """CURVA_MIN% ate t_min, sobe em linha reta ate CURVA_MAX% em t_max (arredonda para cima)."""
    if temp <= t_min:
        return CURVA_MIN
    if temp >= t_max:
        return CURVA_MAX
    pct = CURVA_MIN + (CURVA_MAX - CURVA_MIN) * (temp - t_min) / (t_max - t_min)
    return min(CURVA_MAX, math.ceil(pct / CURVA_PASSO) * CURVA_PASSO)


def ler_estado():
    padrao = {"modo": "auto", "auto_ao_fechar": True, "turbo_auto_critico": False,
              "turbo_liga": TEMP_CRITICA, "turbo_desliga": 75, "curva_min": 60, "curva_max": 85}
    try:
        with open(ESTADO_ARQ, encoding="utf-8") as f:
            padrao.update(json.load(f))
    except Exception:
        pass
    return padrao


def salvar_estado(dados):
    try:
        with open(ESTADO_ARQ, "w", encoding="utf-8") as f:
            json.dump(dados, f)
    except Exception:
        pass


class Monitor:
    """Mantem um PowerShell vivo lendo sensores; entrega dicts via callback."""

    def __init__(self, callback):
        self.callback = callback
        self.proc = None
        threading.Thread(target=self._rodar, daemon=True).start()

    def _rodar(self):
        script = PS_MONITOR.replace("__INTERVALO__", str(INTERVALO))
        try:
            self.proc = subprocess.Popen(ps_args(script), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, bufsize=1, creationflags=NO_WINDOW)
            for linha in self.proc.stdout:
                linha = linha.strip()
                if linha.startswith("{"):
                    try:
                        self.callback(json.loads(linha))
                    except ValueError:
                        pass
        except Exception:
            self.callback(None)

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


# ---------------------------------------------------------------- interface
def cor_temp(t):
    if t is None or t <= 0:
        return COR_SUAVE
    if t >= TEMP_CRITICA:
        return COR_TURBO
    if t >= TEMP_ALERTA:
        return COR_WARN
    return COR_OK


class CardSensor:
    """Card com temperatura grande e RPM; a bolinha colorida liga o card a linha do grafico."""
    W, H = 150, 84

    def __init__(self, pai, titulo, cor_linha):
        self.titulo = titulo
        self.cor_linha = cor_linha
        self.temp = None
        self.rpm = None
        self.c = tk.Canvas(pai, width=self.W, height=self.H, bg=COR_CARD, highlightthickness=0)
        self.desenhar()

    def atualizar(self, temp, rpm):
        self.temp = temp if temp is not None and 0 < temp <= 125 else None
        self.rpm = rpm if rpm is not None and rpm >= 0 else None
        self.desenhar()

    def desenhar(self):
        c, W = self.c, self.W
        c.delete("all")
        cor = cor_temp(self.temp)
        c.create_oval(12, 10, 20, 18, fill=self.cor_linha, outline="")
        c.create_text(26, 14, text=self.titulo, anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9, "bold"))
        if self.temp is not None and self.temp >= TEMP_CRITICA:
            c.create_text(W - 10, 14, text="CRÍTICO", anchor="e", fill=COR_TURBO, font=("Segoe UI", 8, "bold"))
        txt = f"{self.temp}°" if self.temp is not None else "--"
        c.create_text(12, 44, text=txt, anchor="w", fill=cor, font=("Segoe UI", 24, "bold"))
        rpm = f"{self.rpm} RPM" if self.rpm is not None else "RPM --"
        c.create_text(12, 72, text=rpm, anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9))


class Grafico:
    """Temperatura de CPU e GPU nos ultimos HIST_MIN minutos; a escala acompanha os dados."""
    W, H = 308, 150
    ESQ, DIR, TOPO, BASE = 34, 10, 26, 22   # margens

    def __init__(self, pai):
        self.hist = deque(maxlen=HIST_MIN * 60 // INTERVALO)
        self.c = tk.Canvas(pai, width=self.W, height=self.H, bg=COR_CARD, highlightthickness=0)
        self.desenhar()

    def adicionar(self, cpu, gpu):
        self.hist.append((cpu, gpu))
        self.desenhar()

    def desenhar(self):
        c, W, H = self.c, self.W, self.H
        c.delete("all")
        c.create_text(12, 14, text="TEMPERATURA", anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9, "bold"))
        x0, x1, y0, y1 = self.ESQ, W - self.DIR, self.TOPO, H - self.BASE

        vals = [t for par in self.hist for t in par if t is not None]
        if not vals:
            c.create_text(W / 2, (y0 + y1) / 2, text="Lendo sensores...", fill=COR_SUAVE, font=("Segoe UI", 9))
            return

        # escala: margem de 3 °C, multiplos de 5, pelo menos 15 °C de altura
        lo = (min(vals) - 3) // 5 * 5
        hi = -((-(max(vals) + 3)) // 5) * 5
        if hi - lo < 15:
            hi = lo + 15
        passo = 5 if hi - lo <= 30 else 10

        def y_de(t):
            return y1 - (t - lo) / (hi - lo) * (y1 - y0)

        for t in range(int(lo), int(hi) + 1, passo):
            y = y_de(t)
            c.create_line(x0, y, x1, y, fill="#24272c")
            c.create_text(x0 - 6, y, text=f"{t}°", anchor="e", fill=COR_SUAVE, font=("Segoe UI", 7))
        if lo < TEMP_CRITICA <= hi:
            y = y_de(TEMP_CRITICA)
            c.create_line(x0, y, x1, y, fill=COR_TURBO, dash=(3, 3))
        c.create_text(x0, y1 + 12, text=f"-{HIST_MIN} min", anchor="w", fill=COR_SUAVE, font=("Segoe UI", 7))
        c.create_text(x1, y1 + 12, text="agora", anchor="e", fill=COR_SUAVE, font=("Segoe UI", 7))

        n = self.hist.maxlen - 1
        desloc = n + 1 - len(self.hist)   # dados novos entram pela direita
        for i, cor in ((0, COR_CPU), (1, COR_GPU)):
            pts = []
            for k, par in enumerate(self.hist):
                if par[i] is not None:
                    pts.append((x0 + (x1 - x0) * (k + desloc) / n, y_de(par[i])))
            if len(pts) >= 2:
                c.create_line(*[v for p in pts for v in p], fill=cor, width=2)
            if pts:
                x, y = pts[-1]
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=cor, outline="")


MODOS = ("auto", "curva", "turbo")


class Bandeja:
    """Icone na bandeja do sistema com a temperatura mais alta escrita nele."""

    def __init__(self, app):
        self.app = app
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", lambda: self._na_tela(app.mostrar), default=True),
            pystray.Menu.SEPARATOR,
            *[pystray.MenuItem(m.upper(), self._escolher(m), radio=True,
                               checked=lambda item, m=m: app.modo == m) for m in MODOS],
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: self._na_tela(app.fechar)))
        self.icon = pystray.Icon("nitro_fan", self.desenhar(None), "Nitro Fan", menu)
        self.icon.run_detached()

    def _escolher(self, modo):
        return lambda: self._na_tela(lambda: self.app.alternar(modo))

    def _na_tela(self, func):
        self.app.root.after(0, func)  # callbacks do pystray chegam em outra thread

    @staticmethod
    def desenhar(temp):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, 63, 63), radius=12, fill=COR_CARD)
        txt = str(temp) if temp is not None else "--"
        try:
            fonte = ImageFont.truetype("segoeuib.ttf", 44 if len(txt) <= 2 else 32)
        except OSError:
            fonte = ImageFont.load_default()
        d.text((32, 33), txt, font=fonte, fill=cor_temp(temp), anchor="mm")
        return img

    def atualizar(self, temp, dica):
        self.icon.icon = self.desenhar(temp)
        self.icon.title = dica[:127]  # limite do Windows para a dica
        self.icon.update_menu()

    def parar(self):
        self.icon.stop()


class App:
    TAM = 150

    def __init__(self, root):
        self.root = root
        self.estado = ler_estado()
        self.modo = self.estado["modo"]
        # curva so e retomada depois da primeira leitura (precisa da temperatura)
        self.retomar_curva = self.modo == "curva"
        if self.retomar_curva:
            self.modo = "auto"
        self.pct = None             # ultima rotacao gravada no modo curva
        self.enviando_pct = False
        self.ultima_leitura = time.time()
        self.motivo_falha = ""
        self.ocupado = False
        self.angulo = 0.0
        self.velocidade = 0.0
        self.critico = False
        self.ultimo_alerta = 0.0
        self.turbo_foi_automatico = False
        self.auto_pausado = False   # usuario mexeu na mao: espera esfriar antes de agir de novo

        root.title("Nitro Fan")
        root.configure(bg=COR_FUNDO)
        root.resizable(False, False)

        tk.Label(root, text="NITRO FAN", bg=COR_FUNDO, fg=COR_TEXTO,
                 font=("Segoe UI", 16, "bold")).pack(pady=(12, 0))

        self.canvas = tk.Canvas(root, width=self.TAM, height=self.TAM, bg=COR_FUNDO,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(pady=6)
        self.canvas.bind("<Button-1>", lambda e: self.alternar())
        root.bind("<space>", lambda e: self.alternar())

        self.lbl_modo = tk.Label(root, text="", bg=COR_FUNDO, font=("Segoe UI", 20, "bold"))
        self.lbl_modo.pack()
        seletor = tk.Frame(root, bg=COR_CARD)
        seletor.pack(pady=(4, 0))
        self.botoes = {}
        for m in MODOS:
            b = tk.Label(seletor, text=m.upper(), bg=COR_CARD, fg=COR_SUAVE, cursor="hand2",
                         font=("Segoe UI", 9, "bold"), padx=14, pady=3)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, m=m: self.alternar(m))
            self.botoes[m] = b
        self.lbl_status = tk.Label(root, text="", bg=COR_FUNDO, fg=COR_SUAVE, font=("Segoe UI", 9))
        self.lbl_status.pack(pady=(2, 8))

        cards = tk.Frame(root, bg=COR_FUNDO)
        cards.pack(padx=18)
        self.card_cpu = CardSensor(cards, "CPU", COR_CPU)
        self.card_gpu = CardSensor(cards, "GPU", COR_GPU)
        self.card_cpu.c.pack(side="left", padx=(0, 8))
        self.card_gpu.c.pack(side="left")
        self.grafico = Grafico(root)
        self.grafico.c.pack(padx=18, pady=(8, 0))

        opts = tk.Frame(root, bg=COR_FUNDO)
        opts.pack(pady=(10, 12), padx=18, anchor="w")
        self.var_fechar = tk.BooleanVar(value=self.estado["auto_ao_fechar"])
        self.var_auto_crit = tk.BooleanVar(value=self.estado["turbo_auto_critico"])
        self.var_liga = tk.IntVar(value=self.estado["turbo_liga"])
        self.var_desliga = tk.IntVar(value=self.estado["turbo_desliga"])
        estilo = dict(bg=COR_FUNDO, fg=COR_SUAVE, font=("Segoe UI", 9))
        caixa = dict(bg=COR_FUNDO, fg=COR_SUAVE, selectcolor=COR_CARD, activebackground=COR_FUNDO,
                     activeforeground=COR_TEXTO, font=("Segoe UI", 9), bd=0, highlightthickness=0)

        self.var_curva_min = tk.IntVar(value=self.estado["curva_min"])
        self.var_curva_max = tk.IntVar(value=self.estado["curva_max"])
        spin = dict(from_=40, to=100, width=4, bg=COR_CARD, fg=COR_TEXTO, buttonbackground=COR_CARD,
                    insertbackground=COR_TEXTO, relief="flat", font=("Segoe UI", 9), justify="center")

        # ajustes do modo atual: curva no modo CURVA, turbo automatico nos outros
        self.ajustes = tk.Frame(opts, bg=COR_FUNDO)
        self.ajustes.pack(anchor="w", fill="x")
        self.frm_curva = tk.Frame(self.ajustes, bg=COR_FUNDO)
        for i, (texto, var) in enumerate(((f"Curva: {CURVA_MIN}% até", self.var_curva_min),
                                          (f"sobe até {CURVA_MAX}% em", self.var_curva_max))):
            tk.Label(self.frm_curva, text=texto, **estilo).grid(row=i, column=0, sticky="w")
            tk.Spinbox(self.frm_curva, textvariable=var, command=self.ajustar_curva,
                       **spin).grid(row=i, column=1, padx=6, pady=1)
            tk.Label(self.frm_curva, text="°C", **estilo).grid(row=i, column=2, sticky="w")
            var.trace_add("write", lambda *_: self.root.after_idle(self.ajustar_curva))

        self.frm_turbo = tk.Frame(self.ajustes, bg=COR_FUNDO)
        tk.Checkbutton(self.frm_turbo, text="Turbo automático por temperatura", variable=self.var_auto_crit,
                       command=self.persistir, **caixa).pack(anchor="w")
        faixa = tk.Frame(self.frm_turbo, bg=COR_FUNDO)
        faixa.pack(anchor="w", padx=(22, 0), pady=(0, 4))
        for i, (texto, var) in enumerate((("liga acima de", self.var_liga),
                                          ("volta ao AUTO abaixo de", self.var_desliga))):
            tk.Label(faixa, text=texto, **estilo).grid(row=i, column=0, sticky="w")
            tk.Spinbox(faixa, textvariable=var, command=self.ajustar_faixa,
                       **spin).grid(row=i, column=1, padx=6, pady=1)
            tk.Label(faixa, text="°C", **estilo).grid(row=i, column=2, sticky="w")
        for var in (self.var_liga, self.var_desliga):
            var.trace_add("write", lambda *_: self.root.after_idle(self.ajustar_faixa))

        tk.Checkbutton(opts, text="Voltar ao AUTO ao fechar", variable=self.var_fechar,
                       command=self.persistir, **caixa).pack(anchor="w")

        root.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bandeja = Bandeja(self) if pystray else None
        if self.bandeja:  # minimizar esconde na bandeja
            root.bind("<Unmap>", lambda e: e.widget is root and root.state() == "iconic" and root.withdraw())
        self.atualizar_rotulos("Estado salvo da última vez (pode não refletir o hardware)")
        self.monitor = Monitor(lambda d: root.after(0, self.on_sensores, d))
        self.animar()
        self.vigiar()

    # ---- modo dos fans
    def alternar(self, novo=None, origem="manual"):
        if self.ocupado:
            return
        if origem == "manual" and self.var_auto_crit.get():
            self.auto_pausado = True
        if origem == "manual":
            self.retomar_curva = False
        novo = novo or ("auto" if self.modo == "turbo" else "turbo")
        self.ocupado = True
        self.lbl_status.config(text="Aplicando...", fg=COR_SUAVE)
        threading.Thread(target=self._aplicar_thread, args=(novo, origem), daemon=True).start()

    def _aplicar_thread(self, novo, origem):
        ok, msg = aplicar_modo(novo)
        self.root.after(0, self._resultado, novo, ok, msg, origem)

    def _resultado(self, novo, ok, msg, origem):
        self.ocupado = False
        if not ok:
            self.lbl_status.config(text=msg, fg=COR_WARN)
            if novo == "curva":  # nao conseguiu entrar na curva: garante o AUTO
                self.falha_curva(f"Não entrou no modo curva ({msg})")
            return
        self.modo = novo
        self.pct = None
        self.turbo_foi_automatico = (origem == "temperatura" and novo == "turbo")
        self.persistir()
        if origem == "falha":
            self.atualizar_rotulos(self.motivo_falha)
            self.lbl_status.config(fg=COR_WARN)
        elif novo == "curva":
            t_min, t_max = self.curva()
            self.atualizar_rotulos(f"Curva: {CURVA_MIN}% até {t_min}°C, {CURVA_MAX}% em {t_max}°C")
            self.ajustar_rotacao()
        elif origem == "temperatura":
            liga, desliga = self.faixa()
            self.atualizar_rotulos(f"Turbo automático — passou de {liga}°C" if novo == "turbo"
                                   else f"De volta ao AUTO — abaixo de {desliga}°C")
        else:
            self.atualizar_rotulos("Turbo ligado — CPU e GPU no máximo" if novo == "turbo"
                                   else "Controle automático do firmware")

    def atualizar_rotulos(self, status=None):
        cor = {"turbo": COR_TURBO, "curva": COR_CURVA}.get(self.modo, COR_TEXTO)
        self.lbl_modo.config(text=self.nome_modo(), fg=cor)
        for m, b in self.botoes.items():
            b.config(bg=cor if m == self.modo else COR_CARD,
                     fg=COR_FUNDO if m == self.modo else COR_SUAVE)
        if self.modo == "curva":
            self.frm_turbo.pack_forget()
            self.frm_curva.pack(anchor="w", pady=(0, 4))
        else:
            self.frm_curva.pack_forget()
            self.frm_turbo.pack(anchor="w")
        if status is not None:
            self.lbl_status.config(text=status, fg=COR_SUAVE)
        self.atualizar_bandeja()

    def nome_modo(self):
        if self.modo == "curva" and self.pct is not None:
            return f"CURVA · {self.pct}%"
        return self.modo.upper()

    def atualizar_bandeja(self):
        if not self.bandeja:
            return
        cpu, gpu = self.card_cpu, self.card_gpu
        temps = [t for t in (cpu.temp, gpu.temp) if t is not None]
        fmt = lambda v, u: f"{v}{u}" if v is not None else "--"
        dica = (f"Nitro Fan — {self.nome_modo()}\n"
                f"CPU {fmt(cpu.temp, '°C')} · {fmt(cpu.rpm, ' RPM')}\n"
                f"GPU {fmt(gpu.temp, '°C')} · {fmt(gpu.rpm, ' RPM')}")
        self.bandeja.atualizar(max(temps) if temps else None, dica)

    def mostrar(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # ---- sensores
    def on_sensores(self, d):
        if d is None:
            self.lbl_status.config(text="Sensores indisponíveis neste modelo", fg=COR_WARN)
            self.falha_curva("Sensores indisponíveis — voltei ao AUTO")
            return
        self.card_cpu.atualizar(d.get("cpu"), d.get("fcpu"))
        self.card_gpu.atualizar(d.get("gpu"), d.get("fgpu"))
        self.grafico.adicionar(self.card_cpu.temp, self.card_gpu.temp)
        self.atualizar_bandeja()

        temps = [t for t in (self.card_cpu.temp, self.card_gpu.temp) if t is not None]
        maxima = max(temps) if temps else 0
        estava_critico = self.critico
        self.critico = maxima >= TEMP_CRITICA

        if self.critico:
            self.alertar(maxima, primeira_vez=not estava_critico)
        else:
            self.root.title("Nitro Fan")
        if not temps:
            self.falha_curva("Sem leitura de temperatura — voltei ao AUTO")
            return
        self.ultima_leitura = time.time()
        if self.retomar_curva and not self.ocupado:
            self.retomar_curva = False
            self.alternar("curva", origem="retomar")
        elif self.modo == "curva":
            self.ajustar_rotacao(maxima)
        else:
            self.turbo_por_temperatura(maxima)

    # ---- modo curva
    def curva(self):
        """(t_min, t_max) validos; no meio da digitacao usa o ultimo salvo."""
        try:
            t_min, t_max = self.var_curva_min.get(), self.var_curva_max.get()
        except tk.TclError:
            return self.estado["curva_min"], self.estado["curva_max"]
        return t_min, max(t_max, t_min + 5)

    def ajustar_curva(self):
        try:
            t_min, t_max = self.var_curva_min.get(), self.var_curva_max.get()
        except tk.TclError:
            return
        if t_max < t_min + 5:
            self.var_curva_max.set(t_min + 5)
        self.persistir()
        if self.modo == "curva":
            self.atualizar_rotulos(f"Curva: {CURVA_MIN}% até {t_min}°C, {CURVA_MAX}% em {max(t_max, t_min + 5)}°C")

    def ajustar_rotacao(self, maxima=None):
        """Grava a % da curva. Sobe na hora; desce no maximo CURVA_PASSO por leitura."""
        if self.modo != "curva" or self.enviando_pct or self.ocupado:
            return
        if maxima is None:
            temps = [t for t in (self.card_cpu.temp, self.card_gpu.temp) if t is not None]
            maxima = max(temps) if temps else None
        alvo = CURVA_MAX if maxima is None else pct_da_curva(maxima, *self.curva())
        if self.pct is not None and alvo < self.pct:
            alvo = max(alvo, self.pct - CURVA_PASSO)
        if alvo == self.pct:
            return
        self.enviando_pct = True
        threading.Thread(target=lambda: self.root.after(0, self._rotacao_gravada, alvo, *aplicar_rotacao(alvo)),
                         daemon=True).start()

    def _rotacao_gravada(self, pct, ok, msg):
        self.enviando_pct = False
        if self.modo != "curva":
            return
        if not ok:
            self.falha_curva(f"Falha ao gravar a rotação ({msg}) — voltei ao AUTO")
            return
        self.pct = pct
        self.atualizar_rotulos()

    def falha_curva(self, motivo):
        """Qualquer problema no modo curva devolve o controle ao firmware."""
        self.retomar_curva = False
        if self.modo != "curva" or self.ocupado:
            return
        self.motivo_falha = motivo
        self.alternar("auto", origem="falha")

    def vigiar(self):
        if self.modo == "curva" and time.time() - self.ultima_leitura > SEM_LEITURA_MAX:
            self.falha_curva(f"Mais de {SEM_LEITURA_MAX}s sem leitura dos sensores — voltei ao AUTO")
        self.root.after(2000, self.vigiar)

    def faixa(self):
        """(liga, desliga) validos; se o campo estiver no meio da digitacao, usa o ultimo salvo."""
        try:
            liga, desliga = self.var_liga.get(), self.var_desliga.get()
        except tk.TclError:
            return self.estado["turbo_liga"], self.estado["turbo_desliga"]
        return liga, min(desliga, liga - 1)

    def ajustar_faixa(self):
        try:
            liga, desliga = self.var_liga.get(), self.var_desliga.get()
        except tk.TclError:
            return
        if desliga >= liga:
            self.var_desliga.set(liga - 1)
        self.persistir()

    def turbo_por_temperatura(self, maxima):
        """Liga o turbo acima do limite e devolve ao AUTO abaixo do outro (so o turbo que ele ligou)."""
        if not self.var_auto_crit.get() or self.ocupado or self.modo == "curva":
            return
        liga, desliga = self.faixa()
        if maxima < desliga:
            self.auto_pausado = False
        if self.auto_pausado:
            return
        if self.modo != "turbo" and maxima >= liga:
            self.alternar("turbo", origem="temperatura")
        elif self.modo == "turbo" and self.turbo_foi_automatico and maxima < desliga:
            self.alternar("auto", origem="temperatura")

    def alertar(self, temp, primeira_vez):
        self.root.title(f"⚠ {temp}°C — CRÍTICO")
        agora = time.time()
        if primeira_vez or agora - self.ultimo_alerta > 60:  # no maximo 1 aviso/min
            self.ultimo_alerta = agora
            try:
                # com turbo automatico ou curva o app ja esta agindo: so pisca na barra de tarefas
                if not self.var_auto_crit.get() and self.modo != "curva":
                    self.root.deiconify()
                    self.root.lift()
                    self.root.attributes("-topmost", True)
                    self.root.after(1500, lambda: self.root.attributes("-topmost", False))
                    self.root.bell()
                ctypes.windll.user32.FlashWindow(ctypes.windll.user32.GetParent(self.root.winfo_id()), True)
            except Exception:
                pass

    # ---- persistencia / saida
    def persistir(self):
        liga, desliga = self.faixa()
        t_min, t_max = self.curva()
        self.estado.update(turbo_liga=liga, turbo_desliga=desliga, curva_min=t_min, curva_max=t_max)
        modo = "curva" if self.retomar_curva else self.modo  # curva salva que ainda nao foi retomada
        salvar_estado({"modo": modo, "auto_ao_fechar": self.var_fechar.get(),
                       "turbo_auto_critico": self.var_auto_crit.get(),
                       "turbo_liga": liga, "turbo_desliga": desliga,
                       "curva_min": t_min, "curva_max": t_max})

    def fechar(self):
        self.monitor.parar()
        if self.bandeja:
            self.bandeja.parar()
        # a curva depende do app vivo: ao fechar sempre devolve ao AUTO (mas lembra para a proxima)
        sair_da_curva = self.modo == "curva"
        if sair_da_curva or (self.var_fechar.get() and self.modo == "turbo"):
            self.lbl_status.config(text="Voltando ao AUTO...")
            self.root.update()
            ok, _ = aplicar_modo("auto")
            if ok and not sair_da_curva:
                self.modo = "auto"
        self.persistir()
        self.root.destroy()

    # ---- ventoinha animada
    def animar(self):
        if self.modo == "turbo":
            alvo = 18.0
        elif self.modo == "curva" and self.pct is not None:
            alvo = 2.5 + 15.5 * (self.pct - CURVA_MIN) / (CURVA_MAX - CURVA_MIN)
        else:
            alvo = 2.5
        self.velocidade += (alvo - self.velocidade) * 0.06
        self.angulo = (self.angulo + self.velocidade) % 360
        if self.root.state() == "withdrawn":  # escondido na bandeja: nao gasta CPU desenhando
            self.root.after(250, self.animar)
            return
        self.desenhar_fan()
        self.root.after(16, self.animar)

    def desenhar_fan(self):
        c, t = self.canvas, self.TAM
        cx = cy = t / 2
        turbo = self.modo in ("turbo", "curva")   # destaque colorido
        cor = {"turbo": COR_TURBO, "curva": COR_CURVA}.get(self.modo, COR_AUTO)
        c.delete("all")

        if self.modo == "turbo" or self.critico:
            for i, a in enumerate(("#2a0d0e", "#3d1012", "#55151a")):
                r = t / 2 - 2 - i * 3
                c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=a, width=3)

        r_anel = t / 2 - 14
        c.create_oval(cx - r_anel, cy - r_anel, cx + r_anel, cy + r_anel,
                      fill=COR_CARD, outline=cor, width=4)

        n_pas, r_in, r_out = 7, 16, r_anel - 9
        for k in range(n_pas):
            base = math.radians(self.angulo + k * 360 / n_pas)
            pts = []
            for s in range(9):
                f = s / 8
                r = r_in + (r_out - r_in) * f
                a = base + 0.55 * f
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            for s in range(8, -1, -1):
                f = s / 8
                r = r_in + (r_out - r_in) * f
                a = base + 0.55 * f + 0.38 * (1 - 0.4 * f)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            c.create_polygon(pts, fill=cor if turbo else "#5a606a", outline="", smooth=True)

        c.create_oval(cx - 18, cy - 18, cx + 18, cy + 18, fill=COR_FUNDO, outline=cor, width=2)
        c.create_text(cx, cy, text="N", fill=cor if turbo else COR_SUAVE, font=("Segoe UI", 11, "bold"))


def main():
    if os.name != "nt":
        print("Isso so funciona no Windows.")
        return
    if not eh_admin():
        elevar()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
