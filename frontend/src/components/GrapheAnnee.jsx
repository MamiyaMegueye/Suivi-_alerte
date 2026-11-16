import { useEffect, useId, useRef, useState } from 'react';
import { fmtMontant, fmtPct } from '../format.js';

const H = 320; // hauteur en pixels ; la largeur est celle du cadre
const M = { haut: 40, droite: 28, bas: 64, gauche: 70 };

/** Graduation « propre » : 0, 25 M, 50 M… */
function graduations(min, max) {
  const etendue = Math.max(max - min, max * 0.1, 1);
  const brut = etendue / 4;
  const p = 10 ** Math.floor(Math.log10(brut));
  const pas = [1, 2, 2.5, 5, 10].map((k) => k * p).find((k) => k >= brut);
  const debut = Math.max(0, Math.floor(min / pas) * pas - pas);
  const sortie = [];
  for (let v = debut; v <= max + pas * 0.999; v += pas) sortie.push(v);
  if (sortie[sortie.length - 1] < max) sortie.push(sortie[sortie.length - 1] + pas);
  return sortie;
}

function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} Md`;
  if (a >= 1e6) return `${(v / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} M`;
  if (a >= 1e3) return `${(v / 1e3).toLocaleString('fr-FR', { maximumFractionDigits: 0 })} k`;
  return v.toLocaleString('fr-FR');
}

/** Courbe lissée (monotone) passant par les points. */
function courbe(points) {
  if (points.length < 2) return '';
  const n = points.length;
  const d = [];
  const m = [];
  for (let i = 0; i < n - 1; i += 1) d.push((points[i + 1][1] - points[i][1]) / (points[i + 1][0] - points[i][0]));
  m.push(d[0]);
  for (let i = 1; i < n - 1; i += 1) m.push(d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2);
  m.push(d[n - 2]);
  let chemin = `M${points[0][0]},${points[0][1]}`;
  for (let i = 0; i < n - 1; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    const h = (x1 - x0) / 3;
    chemin += ` C${x0 + h},${y0 + m[i] * h} ${x1 - h},${y1 - m[i + 1] * h} ${x1},${y1}`;
  }
  return chemin;
}

function Pastille({ v }) {
  if (v === null || v === undefined) return null;
  const classe = v <= -10 ? 'baisse' : v >= 10 ? 'hausse' : 'stable';
  return (
    <span className={`bulle-var bulle-var-${classe}`}>
      {v > 0 ? '▲ +' : v < 0 ? '▼ −' : ''}
      {fmtPct(Math.abs(v))} sur le mois précédent
    </span>
  );
}

/**
 * Courbe de l'encaissé de chaque mois de l'année, avec une info-bulle au
 * survol (montant et variation sur le mois précédent). Le mois en cours,
 * incomplet, est relié en pointillé.
 */
export default function GrapheAnnee({ titre, valeurs, mois, jourEnCours }) {
  const id = useId().replace(/:/g, '');
  const [survol, setSurvol] = useState(null);
  const cadre = useRef(null);
  const [L, setL] = useState(960);
  // Dessin à la largeur réelle du cadre : le texte garde sa taille.
  useEffect(() => {
    const el = cadre.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([e]) => setL(Math.max(360, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = valeurs.length;
  if (!n) return null;
  const montants = valeurs.map((v) => v.encaisse);
  const echelle = graduations(Math.min(...montants), Math.max(...montants));
  const bas = echelle[0];
  const haut = echelle[echelle.length - 1] || 1;
  const pas = (L - M.gauche - M.droite) / Math.max(1, n - 1);
  const x = (i) => (n === 1 ? (L + M.gauche - M.droite) / 2 : M.gauche + pas * i);
  const y = (v) => M.haut + (H - M.haut - M.bas) * (1 - (v - bas) / (haut - bas || 1));
  const enCours = Boolean(jourEnCours);
  const points = valeurs.map((v, i) => [x(i), y(v.encaisse)]);
  const complets = enCours ? points.slice(0, -1) : points;
  const ligne = courbe(complets);
  const aire = complets.length > 1
    ? `${ligne} L${complets[complets.length - 1][0]},${y(bas)} L${complets[0][0]},${y(bas)} Z`
    : '';
  const iMax = montants.indexOf(Math.max(...montants));
  const iMin = montants.indexOf(Math.min(...montants));
  const etiquetes = new Set([iMax, iMin, n - 1]);
  const total = montants.reduce((s, v) => s + v, 0);
  const s = survol === null ? null : valeurs[survol];

  return (
    <figure className="graphe-annee">
      <div className="graphe-entete">
        <figcaption>{titre}</figcaption>
        <div className="graphe-total">
          <span>Total de l’année</span>
          <strong>{fmtMontant(total)} MRU</strong>
        </div>
      </div>
      <div className="graphe-cadre" ref={cadre}>
        <svg width={L} height={H} viewBox={`0 0 ${L} ${H}`} role="img" aria-label={titre} onMouseLeave={() => setSurvol(null)}>
          <defs>
            <linearGradient id={`aire-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2ec4b6" stopOpacity="0.35" />
              <stop offset="100%" stopColor="#2ec4b6" stopOpacity="0.02" />
            </linearGradient>
          </defs>
          {echelle.map((g) => (
            <g key={g}>
              <line x1={M.gauche} x2={L - M.droite} y1={y(g)} y2={y(g)} className="graphe-grille" />
              <text x={M.gauche - 12} y={y(g)} className="graphe-axe" textAnchor="end" dominantBaseline="middle">
                {compact(g)}
              </text>
            </g>
          ))}
          {aire && <path d={aire} fill={`url(#aire-${id})`} />}
          {ligne && <path d={ligne} className="graphe-ligne" />}
          {enCours && n > 1 && (
            <line x1={points[n - 2][0]} y1={points[n - 2][1]} x2={points[n - 1][0]} y2={points[n - 1][1]} className="graphe-ligne graphe-ligne-en-cours" />
          )}
          {survol !== null && (
            <line x1={x(survol)} x2={x(survol)} y1={M.haut - 10} y2={y(bas)} className="graphe-repere" />
          )}
          {valeurs.map((v, i) => {
            const dernierEnCours = enCours && i === n - 1;
            return (
              <g key={v.mois}>
                <circle
                  cx={x(i)}
                  cy={y(v.encaisse)}
                  r={survol === i ? 7 : 5}
                  className={`graphe-point${dernierEnCours ? ' graphe-point-en-cours' : ''}`}
                />
                {etiquetes.has(i) && (
                  <text x={x(i)} y={y(v.encaisse) - 14} className="graphe-valeur" textAnchor="middle">
                    {compact(v.encaisse)}
                  </text>
                )}
                <text x={x(i)} y={H - M.bas + 24} className="graphe-mois" textAnchor="middle">
                  {mois[i]?.libelle ?? v.mois}
                </text>
                {v.variation_pct !== null && v.variation_pct !== undefined && (
                  <text
                    x={x(i)}
                    y={H - M.bas + 44}
                    textAnchor="middle"
                    className={`graphe-var ${v.variation_pct <= -10 ? 'graphe-var-baisse' : v.variation_pct >= 10 ? 'graphe-var-hausse' : ''}`}
                  >
                    {v.variation_pct > 0 ? '▲' : v.variation_pct < 0 ? '▼' : ''} {fmtPct(Math.abs(v.variation_pct))}
                  </text>
                )}
                <rect
                  x={x(i) - pas / 2}
                  y={0}
                  width={n === 1 ? L : pas}
                  height={H}
                  fill="transparent"
                  onMouseEnter={() => setSurvol(i)}
                  onFocus={() => setSurvol(i)}
                  tabIndex={0}
                  aria-label={`${mois[i]?.libelle} : ${fmtMontant(v.encaisse)} MRU`}
                />
              </g>
            );
          })}
        </svg>
        {s && (
          <div
            className={`graphe-bulle${x(survol) > L * 0.75 ? ' graphe-bulle-gauche' : ''}`}
            style={{ left: x(survol), top: y(s.encaisse) }}
            role="status"
          >
            <strong>
              {mois[survol]?.libelle}
              {enCours && survol === n - 1 ? ` (au ${jourEnCours})` : ''}
            </strong>
            <span className="graphe-bulle-montant">{fmtMontant(s.encaisse)} MRU</span>
            <Pastille v={s.variation_pct} />
          </div>
        )}
      </div>
    </figure>
  );
}
