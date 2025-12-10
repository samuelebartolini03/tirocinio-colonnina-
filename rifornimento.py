import random
import time
import json
import paho.mqtt.client as mqtt
import math

# SISTEMA DI AUTENTICAZIONE

UTENTI = {
    "admin": "1234",
    "tecnico": "password",
    "ospite": "guest"
}


def login():
    print(" ACCESSO SICURO ALLA STAZIONE DI RICARICA")

    tentativi = 3
    while tentativi > 0:
        user = input("Username: ").strip()
        pwd = input("Password: ").strip()

        if user in UTENTI and UTENTI[user] == pwd:
            print("\n Accesso consentito. Benvenuto,", user, "\n")
            return True

        tentativi -= 1
        print(f" Credenziali errate. Tentativi rimasti: {tentativi}")

    print(" Troppi tentativi falliti. Uscita dal sistema.\n")
    exit()


# CONFIGURAZIONE STAZIONE

CONFIG = {
    "max_potenza": 150,               # max potenza per colonnina (kW)
    "soglia_temp_alta": 55,           # in °C (inizia gestione raffreddamento)
    "soglia_temp_critica": 70,        # in °C (sospendi carica)
    "soglia_degrado": 90,             # soglia degrado (sospendi carica)
    "modalita": "Standard",           # Standard, Eco, Boost
    "potenza_massima_stazione": 300,  # kW totale
    "percentuale_riduzione_temp_alta": 0.5,  # riduzione potenza su temp alta (50%)
    "min_power_for_active": 1.0       # minima kW per colonnina considerata "attiva"
}

VEICOLI = {
    "CityCar": {"batteria": 40, "max_potenza": 50},
    "SUV": {"batteria": 80, "max_potenza": 120},
    "Sportiva": {"batteria": 100, "max_potenza": 150}
}

# MQTT
MQTT_BROKER = "localhost"  # Modificare se il broker non è locale
MQTT_PORT = 1883
MQTT_TOPIC_TELEMETRY = "ev/stazione"
MQTT_TOPIC_SERVER = "ev/stazione/server"

client = mqtt.Client()
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    print(f" Connesso al broker MQTT su {MQTT_BROKER}:{MQTT_PORT}")
except Exception as e:
    print(f" ERRORE: Impossibile connettersi al broker MQTT. {e}")
    pass


# SENSORI
class Sensore:
    def __init__(self, tipo):
        self.tipo = tipo

    def rileva(self):
        if self.tipo == "temperatura":
            # Più probabile che la temperatura sia normale (20-40)
            if random.random() < 0.1:
                return round(random.uniform(50, 90), 1)  # Spike
            return round(random.uniform(20, 40), 1)
        elif self.tipo == "temperatura_esterna":
            return round(random.uniform(10, 45), 1)
        elif self.tipo == "degrado":
            return round(random.uniform(5, 15), 1)
        elif self.tipo == "tensione":
            return round(random.uniform(350, 800), 1)


# MODELLO COLONNINA
class Colonnina:
    def __init__(self, id):
        self.id = id
        self.veicolo = None
        self.capacita = None
        self.soc_kwh = 0
        self.carica_attiva = False
        self.stato = "LIBERA"

        self.s_temp = Sensore("temperatura")
        self.s_temp_ext = Sensore("temperatura_esterna")
        self.s_deg = Sensore("degrado")
        self.s_tens = Sensore("tensione")

        self.raffreddamento_attivo = False  # flag se viene applicato raffreddamento locale

    def assegna_auto(self):
        self.veicolo = random.choice(list(VEICOLI.keys()))
        self.capacita = VEICOLI[self.veicolo]["batteria"]
        # Inizia con una carica bassa
        self.soc_kwh = random.uniform(5, 0.3 * self.capacita)
        self.carica_attiva = True
        self.stato = "OCCUPATA"
        self.raffreddamento_attivo = False
        print(f" Nuova auto ({self.veicolo}) sulla colonnina {self.id}")

    def aggiorna_soc(self, potenza_effettiva):
        if self.carica_attiva:
            # Aggiorna SoC in base alla potenza effettivamente erogata
            # si assume potenza_effettiva in kW; tempo di ciclo è 1 minuto -> kWh aggiunti = kW*(1/60)
            self.soc_kwh = min(self.capacita, self.soc_kwh + (potenza_effettiva / 60.0))

            if self.soc_kwh >= self.capacita * 0.98:  # Considera carica completata al 98%
                self.stato = "COMPLETATA"
                self.carica_attiva = False
                print(f" Colonnina {self.id}: ricarica completata. Auto in partenza.")

    def soc_percento(self):
        if not self.veicolo or self.capacita is None:
            return 0
        return round((self.soc_kwh / self.capacita) * 100, 1)

    def leggi_parametri(self):
        if self.stato != "OCCUPATA":
            return {
                "id": self.id,
                "stato": "LIBERA",
                "veicolo": None,
                "soc": 0,
                "temperatura": self.s_temp.rileva(),  # Sensori attivi anche se non carica
                "temperatura_esterna": self.s_temp_ext.rileva(),
                "degrado": self.s_deg.rileva(),
                "tensione": self.s_tens.rileva(),
                "potenza_richiesta": 0
            }
        # Simula una richiesta di potenza basata sul veicolo
        max_potenza_veicolo = VEICOLI[self.veicolo]["max_potenza"]
        # Tende a richiedere il massimo all'inizio
        potenza_richiesta = random.uniform(max_potenza_veicolo * 0.5, max_potenza_veicolo)

        return {
            "id": self.id,
            "veicolo": self.veicolo,
            "stato": self.stato,
            "soc": self.soc_percento(),
            "temperatura": self.s_temp.rileva(),
            "temperatura_esterna": self.s_temp_ext.rileva(),
            "degrado": self.s_deg.rileva(),
            "tensione": self.s_tens.rileva(),
            "potenza_richiesta": round(potenza_richiesta, 1)
        }


# SERVER MULTI-COLONNINA con Logica Intelligente
class StazioneServer:
    def __init__(self):
        # stato del sistema di raffreddamento centrale
        self.raffreddamento_centrale_attivo = False

    def analizza_colonnina_singola(self, p):
        """
        Metodo compatibile con la logica precedente (se volessimo analizzare singolarmente).
        Manteniamo come fallback, ma ora useremo distribuisci_potenza per allocazione intelligente.
        """
        potenza_effettiva = p["potenza_richiesta"]
        azioni = []

        if p["stato"] != "OCCUPATA":
            return ["LIBERA"], 0

        temp = p["temperatura"]
        veicolo = p["veicolo"]

        if CONFIG["modalita"] == "Eco":
            potenza_effettiva *= 0.75
            azioni.append("MODALITA: Eco (-25%)")
        elif CONFIG["modalita"] == "Boost":
            potenza_effettiva *= 1.20
            azioni.append("MODALITA: Boost (+20%)")

        if veicolo and potenza_effettiva > VEICOLI[veicolo]["max_potenza"]:
            potenza_effettiva = VEICOLI[veicolo]["max_potenza"]
            azioni.append("LIMITE: Veicolo Max")

        if temp is not None:
            if CONFIG["soglia_temp_alta"] < temp <= CONFIG["soglia_temp_critica"]:
                potenza_effettiva *= (1 - CONFIG["percentuale_riduzione_temp_alta"])
                azioni.append("RIDUCI: Temp Alta")
            elif temp > CONFIG["soglia_temp_critica"]:
                potenza_effettiva = 0
                azioni.append("FERMA: Temp Critica")

        if p["degrado"] and p["degrado"] > CONFIG["soglia_degrado"]:
            potenza_effettiva = 0
            azioni.append("FERMA: Degrado Alto")

        potenza_effettiva = max(0, min(potenza_effettiva, CONFIG["max_potenza"]))
        if not azioni:
            azioni.append("OK")
        return azioni, round(potenza_effettiva, 1)

    def distribuisci_potenza(self, lista_parametri):
        """
        Algoritmo intelligente di distribuzione della potenza:
        - Priorità a SoC più basso (assegna prima a chi ha poca carica).
        - Se la somma delle richieste supera la potenza massima della stazione,
          mettere a riposo (potenza 0) le colonnine con SoC più alto fino a rientrare.
        - Rispetta limiti veicolo e limiti per temperatura/degrado.
        - Se ci sono temperature alte, attiva raffreddamento centrale (flag) e riduce potenza su quelle colonnine.
        """
        # Copia per non modificare l'input
        dati = [dict(p) for p in lista_parametri if p.get("stato") == "OCCUPATA"]
        risultati = []
        totale_richiesto = 0.0

        # Calcola richiesta nominale (applicando limiti veicolo e modalità) ma senza decidere ancora
        for p in dati:
            req = p.get("potenza_richiesta", 0)
            veicolo = p.get("veicolo")
            # applichiamo modalita' di stazione
            if CONFIG["modalita"] == "Eco":
                req *= 0.75
            elif CONFIG["modalita"] == "Boost":
                req *= 1.2

            # limite veicolo
            if veicolo:
                req = min(req, VEICOLI[veicolo]["max_potenza"])
            # limite colonnina
            req = min(req, CONFIG["max_potenza"])
            p["richiesta_adjusted"] = round(req, 1)
            totale_richiesto += req

        # Controllo temperature per decidere se attivare raffreddamento centrale
        temps = [p["temperatura"] for p in dati if "temperatura" in p]
        necessita_raffreddamento = any(t is not None and t > CONFIG["soglia_temp_alta"] for t in temps)
        self.raffreddamento_centrale_attivo = necessita_raffreddamento

        # Se richiesta complessiva <= capacità stazione, assegniamo proporzionalmente:
        potenza_disponibile = CONFIG["potenza_massima_stazione"]
        assegnazioni = {p["id"]: 0.0 for p in dati}

        # Prima applico sospensioni immediate per casi critici (temp critica / degrado)
        for p in dati:
            idc = p["id"]
            if p["temperatura"] is not None and p["temperatura"] > CONFIG["soglia_temp_critica"]:
                assegnazioni[idc] = 0.0
                p.setdefault("azioni", []).append("FERMA: Temp Critica")
            elif p.get("degrado") and p["degrado"] > CONFIG["soglia_degrado"]:
                assegnazioni[idc] = 0.0
                p.setdefault("azioni", []).append("FERMA: Degrado Alto")

        # Ricalcolo totale richiesto considerando sospensioni critiche
        totale_richiesto_noncritico = sum(p["richiesta_adjusted"] for p in dati if assegnazioni[p["id"]] == 0.0 and p["richiesta_adjusted"] > 0) \
            + sum(p["richiesta_adjusted"] for p in dati if assegnazioni[p["id"]] == 0.0 and p["richiesta_adjusted"] == 0)

        # In pratica è più comodo considerare solo colonnine non sospese
        attive = [p for p in dati if not (p["temperatura"] is not None and p["temperatura"] > CONFIG["soglia_temp_critica"]) and not (p.get("degrado") and p["degrado"] > CONFIG["soglia_degrado"])]

        # Se non ci sono colonnine attive ritorna
        if not attive:
            # costruisco l'output simile all'originale
            out = []
            for p in lista_parametri:
                if p.get("stato") != "OCCUPATA":
                    p["azioni"], p["potenza_effettiva"] = ["LIBERA"], 0
                else:
                    p["azioni"], p["potenza_effettiva"] = ["FERMA: Critico"], 0
                out.append(p)
            return out

        # Ordino per SoC crescente (priorità = meno carica)
        attive_sorted = sorted(attive, key=lambda x: x.get("soc", 100.0))

        # Somma delle richieste attive
        totale_attive_richieste = sum(p["richiesta_adjusted"] for p in attive_sorted)

        # Se la stazione ha abbastanza potenza per soddisfarle tutte:
        if totale_attive_richieste <= potenza_disponibile:
            # assegna esattamente la richiesta (poi gestiamo riduzioni per temp alta)
            for p in attive_sorted:
                idc = p["id"]
                alloc = p["richiesta_adjusted"]
                # riduzione per temperatura alta
                if p["temperatura"] is not None and CONFIG["soglia_temp_alta"] < p["temperatura"] <= CONFIG["soglia_temp_critica"]:
                    alloc *= (1 - CONFIG["percentuale_riduzione_temp_alta"])
                    p.setdefault("azioni", []).append("RIDUCI: Temp Alta (-{})%".format(int(CONFIG["percentuale_riduzione_temp_alta"]*100)))
                    # segnalo raffreddamento locale
                    p.setdefault("raffreddamento", True)
                else:
                    p.setdefault("azioni", []).append("OK")
                assegnazioni[idc] = round(max(0, alloc), 1)
        else:
            # Non abbastanza potenza: assegno priorità ai SoC più bassi.
            # Strategie:
            # 1) assegno a chi ha SoC più basso fino a soddisfare (greedy).
            # 2) le ultime (più cariche) vengono messe a riposo (potenza 0).
            restante = potenza_disponibile
            for p in attive_sorted:
                idc = p["id"]
                richi = p["richiesta_adjusted"]
                # riduzione per temperatura alta (pre-calcolo)
                riduzione = 1.0
                if p["temperatura"] is not None and CONFIG["soglia_temp_alta"] < p["temperatura"] <= CONFIG["soglia_temp_critica"]:
                    riduzione = (1 - CONFIG["percentuale_riduzione_temp_alta"])
                    p.setdefault("azioni", []).append("RIDUCI: Temp Alta (-{})%".format(int(CONFIG["percentuale_riduzione_temp_alta"]*100)))
                    p.setdefault("raffreddamento", True)

                richi_mod = richi * riduzione
                # assegna il minimo tra richiesta e quanto resta
                asseg = min(richi_mod, restante)
                # se non riesco ad assegnare nemmeno la minima considerabile, metto a riposo
                if restante <= 0 or asseg < CONFIG["min_power_for_active"]:
                    assegnazioni[idc] = 0.0
                    p.setdefault("azioni", []).append("RIPOSO: Potenza Non Disponibile")
                else:
                    assegnazioni[idc] = round(max(0.0, asseg), 1)
                    p.setdefault("azioni", []).append("OK (Priorità SOC bassa)")
                    restante -= assegnazioni[idc]

            # Se è rimasta potenza (restante>0), distribuiscila per picchi (es. Boost su veicoli più bisognosi)
            if restante > 0:
                # tentiamo di dare small boost ai primi (più poveri), senza superare richiesta_adjusted
                for p in attive_sorted:
                    idc = p["id"]
                    richi = p["richiesta_adjusted"]
                    current = assegnazioni[idc]
                    max_add = max(0.0, richi - current)
                    if max_add <= 0:
                        continue
                    add = min(max_add, restante)
                    assegnazioni[idc] = round(current + add, 1)
                    restante -= add
                    if restante <= 0:
                        break

        # Ora costruisco risultato finale (includo le libere come prima)
        out = []
        # Mappa delle assegnazioni per lookup
        for p in lista_parametri:
            if p.get("stato") != "OCCUPATA":
                p["azioni"], p["potenza_effettiva"] = ["LIBERA"], 0
            else:
                pid = p["id"]
                # trova assegnazione (0 se non presente)
                pot_eff = assegnazioni.get(pid, 0.0)
                # arrotondo e applico limiti finali
                pot_eff = round(max(0.0, min(pot_eff, CONFIG["max_potenza"])), 1)
                # se non c'erano azioni, segnalo OK
                if "azioni" not in p or not p["azioni"]:
                    p["azioni"] = ["OK"]
                p["potenza_effettiva"] = pot_eff
            out.append(p)

        return out

    def analizza_stazione(self, lista_parametri):
        # Considera solo la potenza EFFETTIVA erogata
        totale = sum(p.get("potenza_effettiva", 0) for p in lista_parametri)
        alert = None

        if totale > CONFIG["potenza_massima_stazione"]:
            alert = f"SOVRACCARICO STAZIONE! Totale {totale:.1f} kW > {CONFIG['potenza_massima_stazione']} kW"

        return alert, round(totale, 1)


# AVVIO STAZIONE
def avvia_stazione(num_colonnine=4):
    colonnine = [Colonnina(i + 1) for i in range(num_colonnine)]
    server = StazioneServer()

    cicli_simulati = 10
    print(f"\n Avvio simulazione per {cicli_simulati} cicli. {num_colonnine} colonnine.")

    for ciclo in range(cicli_simulati):
        print(f"\n --- CICLO {ciclo + 1}/{cicli_simulati} ---")

        parametri_lista = []

        # Prima raccogliamo i parametri (senza ancora applicare potenze)
        for col in colonnine:
            if col.stato == "LIBERA":
                # La colonnina è libera e non c'è un'auto in attesa
                # 40% probabilità che arrivi un'auto in questo ciclo
                if random.random() < 0.4:
                    col.assegna_auto()
                else:
                    print(f" Colonnina {col.id} è LIBERA e in attesa.")
                    # Aggiungi parametri 'vuoti' per la telemetria anche se libera
                    parametri_lista.append(col.leggi_parametri())
                    continue  # Passa alla prossima colonnina

            if col.stato == "COMPLETATA":
                print(f" Colonnina {col.id} è stata liberata.")
                col.stato = "LIBERA"
                col.veicolo = None
                col.raffreddamento_attivo = False
                # Se è appena stata liberata, salta l'analisi e aspetta il prossimo ciclo
                continue

            # Lettura Sensori e Richiesta Potenza
            p = col.leggi_parametri()
            parametri_lista.append(p)

        # Ora applichiamo la logica intelligente di distribuzione della potenza
        parametri_con_potenza = server.distribuisci_potenza(list(parametri_lista))

        # Applichiamo gli aggiornamenti alle colonnine reali e pubblichiamo
        for p in parametri_con_potenza:
            # Trovo la colonnina corrispondente se esiste
            if p.get("stato") != "OCCUPATA":
                # pubblica comunque i dati di colonnina libera
                client.publish(f"ev/stazione/colonnina/{p['id']}", json.dumps(p))
                continue

            # Trova oggetto colonnina
            col = next((c for c in colonnine if c.id == p["id"]), None)
            if not col:
                continue

            # Applica azioni e potenza effettiva
            azioni = p.get("azioni", [])
            potenza_effettiva = p.get("potenza_effettiva", 0.0)

            # segna raffreddamento locale se presente
            if p.get("raffreddamento"):
                col.raffreddamento_attivo = True
            else:
                col.raffreddamento_attivo = False

            # Aggiornamento Carica (usa la potenza effettiva regolata!)
            col.aggiorna_soc(potenza_effettiva)

            # Arricchiamo il payload con SoC aggiornato
            payload = {
                "id": col.id,
                "veicolo": col.veicolo,
                "stato": col.stato,
                "soc": col.soc_percento(),
                "temperatura": p.get("temperatura"),
                "temperatura_esterna": p.get("temperatura_esterna"),
                "degrado": p.get("degrado"),
                "tensione": p.get("tensione"),
                "potenza_richiesta": p.get("potenza_richiesta"),
                "potenza_effettiva": potenza_effettiva,
                "azioni": azioni,
                "raffreddamento_attivo": col.raffreddamento_attivo
            }

            # Stampa un riepilogo per ciclo
            print(
                f"   Col. {col.id} ({payload['veicolo']}): SoC {payload['soc']}% | Potenza Eff. {payload['potenza_effettiva']} kW | Azioni: {', '.join(azioni)} | Temp: {payload['temperatura']}°C")

            # PUBBLICA SUBTOPIC PER NODE-RED (Dati singoli colonnina)
            client.publish(f"ev/stazione/colonnina/{col.id}", json.dumps(payload))

        # Analisi Totale Stazione (basata su potenze effettive già calcolate)
        alert, totale_carica = server.analizza_stazione(parametri_con_potenza)

        server_data = {
            "timestamp": time.time(),
            "totale_carica_kw": totale_carica,
            "alert_stazione": alert,
            "modalita_stazione": CONFIG["modalita"],
            "raffreddamento_centrale_attivo": server.raffreddamento_centrale_attivo
        }

        # PUBBLICA TOPIC TELEMETRIA GENERALE
        client.publish(MQTT_TOPIC_TELEMETRY, json.dumps({"colonnine": parametri_con_potenza}))
        # PUBBLICA TOPIC SERVER (Dati aggregati)
        client.publish(MQTT_TOPIC_SERVER, json.dumps(server_data))

        # Stampa l'esito dell'analisi aggregata
        if alert:
            print(f" ALERT STAZIONE: {alert}")
        if server.raffreddamento_centrale_attivo:
            print(" Sistema di raffreddamento centrale ATTIVO (temperature alte rilevate).")
        print(f" Totale Carica Stazione: {totale_carica} kW")

        time.sleep(2)

    print("\n SIMULAZIONE COMPLETATA")
    client.loop_stop()


# MAIN
if __name__ == "__main__":
    # Puoi cambiare la modalità della stazione qui per testare Eco o Boost
    # CONFIG["modalita"] = "Eco"

    if login():
        avvia_stazione(num_colonnine=4)
