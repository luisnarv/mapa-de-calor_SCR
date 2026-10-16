import json
import pandas as pd
import datetime
import os
import glob

print("Cargando data.json...")
base_path = "Frontend/dashboard/public/scr"
with open(os.path.join(base_path, "data.json"), encoding="utf8") as f:
    main_data = json.load(f)

dim = main_data["dim"]
fecha_min = datetime.datetime.strptime(main_data["meta"]["fecha_min"], "%Y-%m-%d")

print(fecha_min, "-------------->")

def process_pts(pts):
    records = []
    n_rows = len(pts['n'])
    for i in range(n_rows):
        b_idx = pts['b'][i]
        
        # Dimensions lookups
        barrio = dim['barrios'][b_idx] if b_idx < len(dim['barrios']) else "Sin Barrio"
        muni_idx = dim['b_muni'][b_idx] if b_idx < len(dim['b_muni']) else -1
        muni = dim['munis'][muni_idx] if muni_idx >= 0 else "Sin Municipio"
        
        zona_idx = dim['b_zona'][b_idx] if b_idx < len(dim['b_zona']) else -1
        zona = dim['zonas'][zona_idx] if zona_idx >= 0 else "Sin Zona"
        
        estado = dim['estados'][pts['e'][i]]
        tec = dim['tecs'][pts['t'][i]]
        brig = dim['brigs'][pts['g'][i]]
        causa = dim['causas'][pts['c'][i]]
        sub = dim['subs'][pts['s'][i]]
        
        # Time
        m_mins = pts['m'][i]
        dt = fecha_min + datetime.timedelta(minutes=m_mins)
        
        records.append({
            "Orden": pts['n'][i],
            "NIC": pts['nic'][i],
            "Fecha": dt.strftime("%Y-%m-%d"),
            "Hora": dt.strftime("%H:%M"),
            "Estado": estado,
            "Causa": causa,
            "Subaccion": sub,
            "Tecnico": tec,
            "Brigada": brig,
            "Barrio": barrio,
            "Municipio": muni,
            "Zona": zona
        })
    return records

all_records = []
print("Procesando mes en curso (data.json)...")
all_records.extend(process_pts(main_data["pts"]))

# Load historical months
for fpath in glob.glob(os.path.join(base_path, "data_*.json")):
    print(f"Procesando histórico {os.path.basename(fpath)}...")
    with open(fpath, encoding="utf8") as f:
        m_data = json.load(f)
    all_records.extend(process_pts(m_data["pts"]))

df = pd.DataFrame(all_records)
print(f"Total de registros procesados: {len(df)}")

print("Generando reportes...")
df = df.sort_values(by=["Fecha", "Hora"], ascending=[False, False])

# 2. Rendimiento por Técnico
tec_perf = df.groupby(["Tecnico", "Brigada"]).agg(
    Total=("Orden", "count"),
    Efectivas=("Estado", lambda x: (x == "Efectiva").sum()),
    Fallidas=("Estado", lambda x: (x == "Fallida").sum()),
    Perdidas=("Estado", lambda x: (x == "Perdida").sum())
).reset_index()
tec_perf["% Efectividad"] = (tec_perf["Efectivas"] / tec_perf["Total"]).apply(lambda x: f"{x:.1%}")
tec_perf = tec_perf.sort_values(by="Total", ascending=False)

# 3. Análisis Geográfico (Barrios)
geo_perf = df.groupby(["Municipio", "Zona", "Barrio"]).agg(
    Total=("Orden", "count"),
    Efectivas=("Estado", lambda x: (x == "Efectiva").sum()),
    Fallidas=("Estado", lambda x: (x == "Fallida").sum()),
    Perdidas=("Estado", lambda x: (x == "Perdida").sum())
).reset_index()
geo_perf["% Pérdida"] = (geo_perf["Perdidas"] / geo_perf["Total"]).apply(lambda x: f"{x:.1%}")
geo_perf = geo_perf.sort_values(by="Total", ascending=False)

# 4. Pareto de Causales
df_fallidas = df[df["Estado"].isin(["Fallida", "Perdida"])]
pareto = df_fallidas.groupby("Causa").agg(Frecuencia=("Orden", "count")).reset_index()
pareto = pareto.sort_values(by="Frecuencia", ascending=False)
total_fallidas = pareto["Frecuencia"].sum()
pareto["% Impacto"] = (pareto["Frecuencia"] / total_fallidas).apply(lambda x: f"{x:.1%}")

print("Guardando Excel...")
out_path = "Reporte_Operativo_SCR.xlsx"
with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
    tec_perf.to_excel(writer, sheet_name="Rendimiento_Tecnicos", index=False)
    geo_perf.to_excel(writer, sheet_name="Analisis_Geografico", index=False)
    pareto.to_excel(writer, sheet_name="Pareto_Causales", index=False)
    df.to_excel(writer, sheet_name="Historial_Completo", index=False)

print(f"Reporte generado exitosamente en: {out_path}")
