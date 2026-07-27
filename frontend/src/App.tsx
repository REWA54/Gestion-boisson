import {
  ArrowLeftRight,
  Amphora as Bottle,
  Box,
  Camera,
  Check,
  ChevronRight,
  CircleUserRound,
  CloudOff,
  Download,
  GlassWater,
  Heart,
  Home,
  LoaderCircle,
  LocateFixed,
  LogOut,
  MapPin,
  Minus,
  MoreHorizontal,
  PackagePlus,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  Sparkles,
  Star,
  Users,
  Warehouse,
  Wifi,
  WifiOff,
  Wine,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, getToken, mutation, setToken, syncQueue } from "./api";
import { clearLocalData, getQueue } from "./offline";
import packageMetadata from "../package.json";
import type {
  Beverage,
  Collection,
  Dashboard,
  EventItem,
  Location,
  User,
  Variant,
} from "./types";

type View = "home" | "inventory" | "scan" | "journal" | "more";
type Toast = { id: number; message: string; tone: "success" | "error" | "info" };
type ActionMode = "withdraw" | "move" | "reserve" | "taste" | null;
const USER_CACHE_KEY = "cellier-user";

const categories: Record<string, { label: string; color: string }> = {
  wine: { label: "Vin", color: "#8f354c" },
  sparkling: { label: "Effervescent", color: "#b58a32" },
  beer: { label: "Bière", color: "#b2662f" },
  cider: { label: "Cidre", color: "#8b8a32" },
  spirit: { label: "Spiritueux", color: "#6e4b34" },
  liqueur: { label: "Liqueur", color: "#69436d" },
  non_alcoholic: { label: "Sans alcool", color: "#36766e" },
  other: { label: "Autre", color: "#59636b" },
};

const eventLabels: Record<string, string> = {
  add: "Stock ajouté",
  withdraw: "Boisson retirée",
  open: "Contenant ouvert",
  move: "Stock déplacé",
  undo: "Action annulée",
  redo: "Action rétablie",
};

function messageFrom(error: unknown): string {
  if (error instanceof ApiError) {
    if (typeof error.detail === "string") return error.detail;
    if (error.detail && typeof error.detail === "object" && "message" in error.detail) {
      return String((error.detail as { message: unknown }).message);
    }
  }
  return error instanceof Error ? error.message : "Une erreur inattendue est survenue";
}

function normalize(value: string): string {
  return value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
}

function formatVariant(variant: Variant): string {
  return [
    variant.vintage,
    variant.volume_ml ? `${variant.volume_ml / 10} cl` : "",
    variant.edition,
  ].filter(Boolean).join(" · ");
}

function has(user: User, permission: string): boolean {
  return user.is_admin || Boolean(user.permissions[permission]);
}

function IconButton({
  children,
  label,
  onClick,
  className = "",
}: {
  children: ReactNode;
  label: string;
  onClick: () => void;
  className?: string;
}) {
  return <button className={`icon-button ${className}`} aria-label={label} onClick={onClick}>{children}</button>;
}

function AuthScreen({
  needsSetup,
  onAuthenticated,
}: {
  needsSetup: boolean;
  onAuthenticated: (user: User) => void;
}) {
  const [setup, setSetup] = useState(needsSetup);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ token: string; user: User }>(
        setup ? "/api/auth/setup" : "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify(setup ? {
            display_name: form.get("display_name"),
            username: form.get("username"),
            password: form.get("password"),
            collection_name: form.get("collection_name"),
          } : {
            username: form.get("username"),
            password: form.get("password"),
          }),
        },
      );
      await clearLocalData();
      setToken(result.token);
      localStorage.setItem(USER_CACHE_KEY, JSON.stringify(result.user));
      onAuthenticated(result.user);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-brand">
        <div className="brand-mark"><Bottle size={34} /></div>
        <div>
          <p className="eyebrow">LOCAL · PRIVÉ · FIABLE</p>
          <h1>Cellier</h1>
          <p>Toutes vos boissons, exactement là où vous les avez rangées.</p>
        </div>
      </section>
      <section className="auth-card">
        <div>
          <p className="eyebrow">{setup ? "PREMIÈRE OUVERTURE" : "BON RETOUR"}</p>
          <h2>{setup ? "Créer votre cellier" : "Se connecter"}</h2>
          <p className="muted">
            {setup
              ? "Le premier compte sera administrateur de l’installation."
              : "Votre session restera active sur cet appareil."}
          </p>
        </div>
        <form onSubmit={submit} className="stack">
          {setup && (
            <label>Votre nom
              <input name="display_name" autoComplete="name" required placeholder="Lauris" />
            </label>
          )}
          <label>Identifiant
            <input name="username" autoComplete="username" required minLength={3} placeholder="lauris" />
          </label>
          <label>Mot de passe
            <input name="password" type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={10} />
          </label>
          {setup && (
            <label>Première collection
              <input name="collection_name" required defaultValue="Ma cave" />
            </label>
          )}
          {error && <div className="form-error">{error}</div>}
          <button className="button primary wide" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={18} />}
            {setup ? "Créer mon espace" : "Entrer dans Cellier"}
          </button>
        </form>
        {!needsSetup && (
          <button className="link-button" onClick={() => setSetup(false)}>Connexion à une installation existante</button>
        )}
      </section>
    </main>
  );
}

function BeverageCard({ item, onClick }: { item: Beverage; onClick: () => void }) {
  const variant = item.variants[0];
  const paths = Array.from(new Set(item.variants.flatMap((v) => v.positions.map((p) => p.location_path))));
  const category = categories[item.category] || categories.other;
  return (
    <button className="beverage-card" onClick={onClick}>
      <div className="bottle-art" style={{ "--drink": category.color } as React.CSSProperties}>
        {item.photo_path ? <img src={item.photo_path} alt="" /> : <Wine size={28} />}
      </div>
      <div className="beverage-main">
        <div className="card-line">
          <span className="category-dot" style={{ background: category.color }} />
          <span className="category-name">{category.label}</span>
          {variant?.open_containers > 0 && <span className="open-pill">Ouvert</span>}
        </div>
        <h3>{item.name}</h3>
        <p>{[item.producer, variant && formatVariant(variant)].filter(Boolean).join(" · ") || "Référence personnelle"}</p>
        <div className="location-line">
          <MapPin size={14} />
          <span>{paths[0] || "Pas encore rangé"}</span>
          {paths.length > 1 && <b>+{paths.length - 1}</b>}
        </div>
      </div>
      <div className="quantity-badge"><strong>{item.quantity}</strong><small>unités</small></div>
      <ChevronRight size={18} className="chevron" />
    </button>
  );
}

function EmptyState({ icon, title, text, action }: { icon: ReactNode; title: string; text: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}

function AddReferenceSheet({
  collections,
  locations,
  onClose,
  onCreated,
}: {
  collections: Collection[];
  locations: Location[];
  onClose: () => void;
  onCreated: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [collectionId, setCollectionId] = useState(collections[0]?.id || 0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      let photoPath: string | null = null;
      const photo = form.get("photo");
      if (photo instanceof File && photo.size && navigator.onLine) {
        const body = new FormData();
        body.append("file", photo);
        const upload = await api<{ path: string }>("/api/media", { method: "POST", body });
        photoPath = upload.path;
      }
      const referencePayload = {
          collection_id: collectionId,
          name: form.get("name"),
          producer: form.get("producer"),
          category: form.get("category"),
          country: form.get("country"),
          region: form.get("region"),
          barcode: form.get("barcode") || null,
          alcohol_percent: form.get("alcohol_percent") ? Number(form.get("alcohol_percent")) : null,
          tags: String(form.get("tags") || "").split(",").map((x) => x.trim()).filter(Boolean),
          variant: {
            vintage: form.get("vintage"),
            volume_ml: Number(form.get("volume_ml")) || null,
            format: form.get("format"),
          },
      };
      const quantity = Number(form.get("quantity"));
      const locationId = Number(form.get("location_id"));
      const result = await mutation<{ reference_id: number }>(
        "reference_create",
        "/api/offline/reference-create",
        {
          reference: referencePayload,
          photo_path: photoPath,
          stock: quantity > 0 && locationId ? {
          location_id: locationId,
          quantity,
          packaging: form.get("packaging"),
          units_per_package: Number(form.get("units_per_package")) || 1,
          package_state: form.get("package_state"),
          unit_price_cents: form.get("unit_price")
            ? Math.round(Number(form.get("unit_price")) * 100)
            : null,
          seller: form.get("seller"),
          terminal: navigator.userAgent.slice(0, 120),
          } : null,
          terminal: navigator.userAgent.slice(0, 120),
        },
      );
      onCreated(result.queued ? "Ajout enregistré hors ligne" : "Boisson ajoutée");
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  }

  const possibleLocations = locations.filter((x) => x.collection_id === collectionId && x.is_terminal);
  return (
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="sheet large-sheet">
        <header className="sheet-header">
          <div>
            <p className="eyebrow">NOUVELLE ENTRÉE</p>
            <h2>Ajouter une boisson</h2>
          </div>
          <IconButton label="Fermer" onClick={onClose}><X /></IconButton>
        </header>
        <form onSubmit={submit} className="form-grid">
          <label className="photo-field">
            <Camera size={28} />
            <span>Photographier l’étiquette</span>
            <input name="photo" type="file" accept="image/*" capture="environment" />
          </label>
          <label>Collection
            <select value={collectionId} onChange={(e) => setCollectionId(Number(e.target.value))}>
              {collections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="span-2">Nom *
            <input name="name" required autoFocus placeholder="Cuvée, nom commercial…" />
          </label>
          <label>Producteur
            <input name="producer" placeholder="Domaine, brasserie, marque…" />
          </label>
          <label>Catégorie
            <select name="category" defaultValue="wine">
              {Object.entries(categories).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
            </select>
          </label>
          <label>Millésime / année
            <input name="vintage" inputMode="numeric" placeholder="2022" />
          </label>
          <label>Volume
            <select name="volume_ml" defaultValue="750">
              <option value="330">33 cl</option><option value="500">50 cl</option>
              <option value="700">70 cl</option><option value="750">75 cl</option>
              <option value="1500">Magnum · 1,5 L</option>
            </select>
          </label>
          <label>Pays
            <input name="country" placeholder="France" />
          </label>
          <label>Région
            <input name="region" placeholder="Bourgogne" />
          </label>
          <label>Code-barres
            <input name="barcode" inputMode="numeric" />
          </label>
          <label>Alcool
            <div className="input-suffix"><input name="alcohol_percent" type="number" step="0.1" min="0" max="100" /><span>%</span></div>
          </label>
          <label className="span-2">Tags
            <input name="tags" placeholder="raclette, garde, favori" />
          </label>
          <div className="section-divider span-2"><span>Entrée en stock</span></div>
          <label>Quantité
            <input name="quantity" type="number" min="0" defaultValue="1" />
          </label>
          <label>Emplacement
            <select name="location_id">
              <option value="">À ranger plus tard</option>
              {possibleLocations.map((item) => <option key={item.id} value={item.id}>{item.path}</option>)}
            </select>
          </label>
          <label>Conditionnement
            <select name="packaging" defaultValue="unit">
              <option value="unit">Unité</option><option value="box">Carton</option>
              <option value="case">Caisse</option><option value="pack">Pack</option>
              <option value="gift_box">Coffret</option>
            </select>
          </label>
          <label>Unités par conditionnement
            <input name="units_per_package" type="number" min="1" defaultValue="1" />
          </label>
          <label>État
            <select name="package_state"><option value="open">Ouvert</option><option value="closed">Fermé</option></select>
          </label>
          <label>Prix unitaire
            <div className="input-suffix"><input name="unit_price" type="number" min="0" step="0.01" /><span>€</span></div>
          </label>
          <label className="span-2">Vendeur
            <input name="seller" />
          </label>
          {error && <div className="form-error span-2">{error}</div>}
          <div className="sheet-actions span-2">
            <button type="button" className="button ghost" onClick={onClose}>Annuler</button>
            <button className="button primary" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={18} /> : <PackagePlus size={18} />} Ajouter
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function ActionSheet({
  item,
  mode,
  locations,
  onClose,
  onDone,
}: {
  item: Beverage;
  mode: Exclude<ActionMode, null>;
  locations: Location[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const variant = item.variants[0];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [source, setSource] = useState(variant.positions[0]?.location_id || 0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      if (mode === "withdraw") {
        const result = await mutation("withdraw", "/api/stock/withdraw", {
          variant_id: variant.id,
          location_id: source || null,
          quantity: Number(form.get("quantity")),
          open_container: form.get("open_container") === "on",
          terminal: navigator.userAgent.slice(0, 120),
        });
        onDone(result.queued ? "Retrait enregistré hors ligne" : "Boisson retirée");
      } else if (mode === "move") {
        const result = await mutation("move", "/api/stock/move", {
          variant_id: variant.id,
          source_location_id: source,
          target_location_id: Number(form.get("target")),
          quantity: Number(form.get("quantity")),
          collision_strategy: "reject",
          terminal: navigator.userAgent.slice(0, 120),
        });
        onDone(result.queued ? "Déplacement enregistré hors ligne" : "Boisson déplacée");
      } else if (mode === "reserve") {
        const result = await mutation("reserve", "/api/offline/reserve", {
            variant_id: variant.id,
            quantity: Number(form.get("quantity")),
            planned_for: form.get("planned_for") || null,
            occasion: form.get("occasion"),
            comment: form.get("comment"),
        });
        onDone(result.queued ? "Réservation enregistrée hors ligne" : "Réservation créée");
      } else {
        const result = await mutation("taste", "/api/offline/taste", {
            variant_id: variant.id,
            sentiment: form.get("sentiment"),
            comment: form.get("comment"),
            meal: form.get("meal"),
            occasion: form.get("occasion"),
            people: "",
            visibility: form.get("visibility"),
        });
        onDone(result.queued ? "Dégustation enregistrée hors ligne" : "Dégustation ajoutée");
      }
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  }

  const title = { withdraw: "Retirer du stock", move: "Déplacer", reserve: "Réserver", taste: "Ajouter une dégustation" }[mode];
  const destinationLocations = locations.filter((x) =>
    x.collection_id === item.collection_id && x.is_terminal && x.id !== source,
  );
  return (
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="sheet compact-sheet">
        <header className="sheet-header">
          <div><p className="eyebrow">{item.name}</p><h2>{title}</h2></div>
          <IconButton label="Fermer" onClick={onClose}><X /></IconButton>
        </header>
        <form onSubmit={submit} className="stack">
          {mode !== "taste" && <label>Quantité
            <input name="quantity" type="number" min="1" max={Math.max(variant.quantity, 1)} defaultValue="1" required />
          </label>}
          {(mode === "withdraw" || mode === "move") && <label>Depuis
            <select value={source} onChange={(e) => setSource(Number(e.target.value))} required>
              {variant.positions.map((position) => (
                <option key={position.id} value={position.location_id}>{position.location_path} · {position.quantity}</option>
              ))}
            </select>
          </label>}
          {mode === "withdraw" && item.category === "spirit" && (
            <label className="check-row"><input type="checkbox" name="open_container" /> Ouvrir et conserver comme contenant entamé</label>
          )}
          {mode === "move" && <label>Vers
            <select name="target" required>
              <option value="">Choisir un emplacement</option>
              {destinationLocations.map((location) => (
                <option key={location.id} value={location.id}>{location.path}{location.occupied ? " · occupé" : ""}</option>
              ))}
            </select>
          </label>}
          {mode === "reserve" && <>
            <label>Date prévue<input type="datetime-local" name="planned_for" /></label>
            <label>Occasion<input name="occasion" placeholder="Dîner, anniversaire…" /></label>
            <label>Commentaire<textarea name="comment" rows={3} /></label>
          </>}
          {mode === "taste" && <>
            <fieldset className="sentiments">
              <legend>Votre avis</legend>
              <label><input type="radio" name="sentiment" value="liked" required /><Heart /> Aimé</label>
              <label><input type="radio" name="sentiment" value="neutral" /><GlassWater /> Neutre</label>
              <label><input type="radio" name="sentiment" value="disliked" /><X /> Pas aimé</label>
            </fieldset>
            <label>Commentaire<textarea name="comment" rows={4} /></label>
            <label>Repas associé<input name="meal" /></label>
            <label>Occasion<input name="occasion" /></label>
            <label>Visibilité<select name="visibility"><option value="private">Privée</option><option value="family">Toute la famille</option></select></label>
          </>}
          {error && <div className="form-error">{error}</div>}
          <button className="button primary wide" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={18} />} Confirmer
          </button>
        </form>
      </section>
    </div>
  );
}

function BeverageDetail({
  item,
  user,
  onClose,
  onAction,
}: {
  item: Beverage;
  user: User;
  onClose: () => void;
  onAction: (mode: Exclude<ActionMode, null>) => void;
}) {
  const variant = item.variants[0];
  const category = categories[item.category] || categories.other;
  return (
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="sheet detail-sheet">
        <div className="detail-hero" style={{ "--drink": category.color } as React.CSSProperties}>
          <IconButton label="Fermer" onClick={onClose} className="floating-close"><X /></IconButton>
          {item.photo_path ? <img src={item.photo_path} alt={`Étiquette ${item.name}`} /> : <Wine size={72} />}
        </div>
        <div className="detail-content">
          <div className="card-line"><span className="category-dot" style={{ background: category.color }} /><span>{category.label}</span></div>
          <h2>{item.name}</h2>
          <p className="detail-producer">{[item.producer, variant && formatVariant(variant)].filter(Boolean).join(" · ")}</p>
          <div className="detail-stock">
            <div><strong>{item.quantity}</strong><span>disponibles</span></div>
            <div><strong>{variant?.reserved_quantity || 0}</strong><span>réservées</span></div>
            <div><strong>{variant?.open_containers || 0}</strong><span>ouvertes</span></div>
          </div>
          <div className="primary-actions">
            {has(user, "stock:withdraw") && <button className="button primary action-big" disabled={!item.quantity} onClick={() => onAction("withdraw")}><Minus /> Retirer 1</button>}
            {has(user, "stock:move") && <button className="button secondary action-big" disabled={!item.quantity} onClick={() => onAction("move")}><ArrowLeftRight /> Déplacer</button>}
          </div>
          <div className="secondary-actions">
            {has(user, "reservation:create") && <button onClick={() => onAction("reserve")}><Star /> Réserver</button>}
            {has(user, "tasting:add") && <button onClick={() => onAction("taste")}><Heart /> Déguster</button>}
          </div>
          <section className="detail-section">
            <h3><MapPin size={18} /> Emplacements</h3>
            {variant?.positions.length ? variant.positions.map((position) => (
              <div className="position-row" key={position.id}>
                <div><strong>{position.location_name}</strong><small>{position.location_path}</small></div>
                <span>{position.quantity}</span>
              </div>
            )) : <p className="muted">Cette référence n’est pas en stock.</p>}
          </section>
          {(item.region || item.country || item.alcohol_percent) && <section className="detail-section">
            <h3>Informations</h3>
            <dl className="facts">
              {item.country && <><dt>Pays</dt><dd>{item.country}</dd></>}
              {item.region && <><dt>Région</dt><dd>{item.region}</dd></>}
              {item.alcohol_percent != null && <><dt>Alcool</dt><dd>{item.alcohol_percent} %</dd></>}
            </dl>
          </section>}
        </div>
      </section>
    </div>
  );
}

function ScanView({
  onAdd,
  onFound,
}: {
  onAdd: () => void;
  onFound: (id: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<{ reference_id?: number; name: string; producer: string; confidence: number; source?: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [photo, setPhoto] = useState<string | null>(null);

  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const result = await api<{ results: typeof results }>(`/api/recognition?q=${encodeURIComponent(query)}`);
      setResults(result.results);
    } finally {
      setBusy(false);
    }
  }

  async function selectPhoto(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setPhoto(URL.createObjectURL(file));
    if (!navigator.onLine) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await api<{ ocr_text: string; results: typeof results }>(
        "/api/recognition/photo",
        { method: "POST", body },
      );
      setQuery(result.ocr_text);
      setResults(result.results);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view scan-view">
      <header className="view-header center-header">
        <p className="eyebrow">RECONNAISSANCE LOCALE D’ABORD</p>
        <h1>Scanner une boisson</h1>
        <p>Une confirmation sera toujours demandée avant l’ajout.</p>
      </header>
      <label className={`camera-zone ${photo ? "has-photo" : ""}`} style={photo ? { backgroundImage: `url(${photo})` } : undefined}>
        <input type="file" accept="image/*" capture="environment" onChange={selectPhoto} />
        {!photo && <><Camera size={54} /><strong>Photographier l’étiquette</strong><span>Cadrez la face avant de la bouteille</span></>}
        {photo && <span className="retake">Reprendre la photo</span>}
        <div className="camera-corners" />
      </label>
      <div className="or-divider"><span>ou identifier par texte / code-barres</span></div>
      <div className="scan-search">
        <Search />
        <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="Nom, producteur ou code-barres" />
        <button onClick={search} disabled={busy}>{busy ? <LoaderCircle className="spin" /> : "Chercher"}</button>
      </div>
      {results.length > 0 && <section className="recognition-results">
        <h2>Résultats probables</h2>
        {results.map((result) => (
          <button key={`${result.reference_id || result.name}-${result.producer}`} onClick={() => result.reference_id ? onFound(result.reference_id) : onAdd()}>
            <div className="result-icon"><Bottle /></div>
            <div><strong>{result.name}</strong><span>{result.producer || "Référence locale"}</span></div>
            <small>{Math.round(result.confidence * 100)} %</small><ChevronRight />
          </button>
        ))}
      </section>}
      {(photo || (query && !busy && results.length === 0)) && (
        <button className="button secondary wide" onClick={onAdd}><Plus /> Saisir comme nouvelle référence</button>
      )}
      <p className="privacy-note"><CloudOff size={16} /> Vos photos restent sur votre serveur tant qu’aucun fournisseur externe n’est activé.</p>
    </div>
  );
}

function MoreView({
  user,
  locations,
  collections,
  dashboard,
  onRefresh,
  notify,
  logout,
}: {
  user: User;
  locations: Location[];
  collections: Collection[];
  dashboard: Dashboard | null;
  onRefresh: () => void;
  notify: (message: string, tone?: Toast["tone"]) => void;
  logout: () => void;
}) {
  const [tab, setTab] = useState<"locations" | "reservations" | "tastings" | "settings">("locations");
  const [reservations, setReservations] = useState<any[]>([]);
  const [tastings, setTastings] = useState<any[]>([]);
  const [addingLocation, setAddingLocation] = useState(false);

  useEffect(() => {
    if (tab === "reservations") api<any[]>("/api/reservations", {}, "reservations").then(setReservations).catch(() => {});
    if (tab === "tastings") api<any[]>("/api/tastings", {}, "tastings").then(setTastings).catch(() => {});
  }, [tab]);

  async function addLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api("/api/locations", {
        method: "POST",
        body: JSON.stringify({
          collection_id: Number(form.get("collection_id")),
          parent_id: form.get("parent_id") ? Number(form.get("parent_id")) : null,
          name: form.get("name"),
          kind: form.get("kind"),
          qr_code: form.get("qr_code") || null,
          is_terminal: form.get("is_terminal") === "on",
        }),
      });
      setAddingLocation(false);
      onRefresh();
      notify("Emplacement créé");
    } catch (error) {
      notify(messageFrom(error), "error");
    }
  }

  async function toggleParty() {
    try {
      await api("/api/settings/party-mode", {
        method: "PUT",
        body: JSON.stringify({ enabled: !dashboard?.party_mode }),
      });
      onRefresh();
      notify(dashboard?.party_mode ? "Mode soirée désactivé" : "Mode soirée activé");
    } catch (error) {
      notify(messageFrom(error), "error");
    }
  }

  async function exportData() {
    try {
      const data = await api<Record<string, unknown>>("/api/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `cellier-export-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      notify(messageFrom(error), "error");
    }
  }

  return (
    <div className="view">
      <header className="view-header">
        <p className="eyebrow">VOTRE ESPACE</p>
        <h1>Plus</h1>
      </header>
      <div className="profile-card">
        <div className="avatar">{user.display_name.slice(0, 1).toUpperCase()}</div>
        <div><strong>{user.display_name}</strong><span>{user.role === "admin" ? "Administrateur" : user.role}</span></div>
        <button onClick={logout}><LogOut size={18} /> Déconnexion</button>
      </div>
      <div className="segmented">
        <button className={tab === "locations" ? "active" : ""} onClick={() => setTab("locations")}><Warehouse /> Lieux</button>
        <button className={tab === "reservations" ? "active" : ""} onClick={() => setTab("reservations")}><Star /> Réservations</button>
        <button className={tab === "tastings" ? "active" : ""} onClick={() => setTab("tastings")}><Heart /> Dégustations</button>
        <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}><Settings /> Réglages</button>
      </div>
      {tab === "locations" && <section className="panel">
        <div className="panel-heading"><div><h2>Emplacements</h2><p>Une arborescence libre pour chaque collection.</p></div>
          {has(user, "location:manage") && <button className="button small secondary" onClick={() => setAddingLocation(!addingLocation)}><Plus /> Ajouter</button>}
        </div>
        {addingLocation && <form className="inline-form" onSubmit={addLocation}>
          <label>Collection<select name="collection_id">{collections.map((x) => <option value={x.id} key={x.id}>{x.name}</option>)}</select></label>
          <label>Parent<select name="parent_id"><option value="">Aucun</option>{locations.map((x) => <option value={x.id} key={x.id}>{x.path}</option>)}</select></label>
          <label>Nom<input name="name" required /></label>
          <label>Type<select name="kind"><option value="place">Lieu</option><option value="zone">Zone</option><option value="rack">Rack</option><option value="shelf">Étagère</option><option value="location">Emplacement</option><option value="box">Carton</option></select></label>
          <label>QR code<input name="qr_code" /></label>
          <label className="check-row"><input name="is_terminal" type="checkbox" defaultChecked /> Emplacement final</label>
          <button className="button primary">Créer</button>
        </form>}
        <div className="location-list">
          {locations.map((location) => <div key={location.id}>
            <div className={`location-kind ${location.kind}`}><MapPin /></div>
            <div><strong>{location.name}</strong><span>{location.path}</span></div>
            <small className={location.occupied ? "occupied" : ""}>{location.occupied ? "Occupé" : "Libre"}</small>
          </div>)}
          {!locations.length && <EmptyState icon={<MapPin />} title="Aucun emplacement" text="Créez votre premier lieu puis ses zones de rangement." />}
        </div>
      </section>}
      {tab === "reservations" && <section className="panel">
        <div className="panel-heading"><div><h2>Réservations</h2><p>Quantités mises de côté pour plus tard.</p></div></div>
        <div className="feed">
          {reservations.map((item) => <article key={item.id}><div className="feed-icon"><Star /></div><div><strong>{item.reference_name} {item.vintage}</strong><span>{item.quantity} unité(s) · {item.user_name}{item.occasion ? ` · ${item.occasion}` : ""}</span></div></article>)}
          {!reservations.length && <EmptyState icon={<Star />} title="Aucune réservation" text="Les bouteilles réservées apparaîtront ici." />}
        </div>
      </section>}
      {tab === "tastings" && <section className="panel">
        <div className="panel-heading"><div><h2>Journal de dégustation</h2><p>Simple, personnel et privé par défaut.</p></div></div>
        <div className="feed">
          {tastings.map((item) => <article key={item.id}><div className={`feed-icon ${item.sentiment}`}><Heart /></div><div><strong>{item.reference_name} {item.vintage}</strong><span>{item.comment || (item.sentiment === "liked" ? "Aimé" : item.sentiment === "neutral" ? "Neutre" : "Pas aimé")}</span><small>{item.user_name}</small></div></article>)}
          {!tastings.length && <EmptyState icon={<Heart />} title="Aucune dégustation" text="Ajoutez votre avis depuis la fiche d’une boisson." />}
        </div>
      </section>}
      {tab === "settings" && <section className="settings-list">
        <button className="setting-row" onClick={toggleParty}>
          <div className="setting-icon party"><Sparkles /></div><div><strong>Mode soirée</strong><span>Interface simplifiée, finances masquées</span></div>
          <span className={`toggle ${dashboard?.party_mode ? "on" : ""}`} />
        </button>
        {has(user, "data:export") && <button className="setting-row" onClick={exportData}>
          <div className="setting-icon"><Download /></div><div><strong>Exporter les données</strong><span>Sauvegarde JSON portable</span></div><ChevronRight />
        </button>}
        <div className="setting-row static">
          <div className="setting-icon"><Wifi /></div><div><strong>Local-first</strong><span>Les actions hors ligne sont synchronisées automatiquement</span></div><Check />
        </div>
        <div className="about-card"><Bottle /><div><strong>Cellier {packageMetadata.version}</strong><span>Open source · AGPL-3.0</span></div></div>
      </section>}
    </div>
  );
}

export default function App() {
  const [booting, setBooting] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<View>("home");
  const [online, setOnline] = useState(navigator.onLine);
  const [pending, setPending] = useState(0);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [beverages, setBeverages] = useState<Beverage[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [selected, setSelected] = useState<Beverage | null>(null);
  const [actionMode, setActionMode] = useState<ActionMode>(null);
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [collectionFilter, setCollectionFilter] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [partyChanging, setPartyChanging] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((message: string, tone: Toast["tone"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, tone }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4000);
  }, []);

  const refreshQueueCount = useCallback(async () => {
    try {
      setPending((await getQueue()).filter((x) => x.status === "pending").length);
    } catch { /* IndexedDB may be disabled in private contexts. */ }
  }, []);

  const loadData = useCallback(async (quiet = false) => {
    if (!getToken()) return;
    if (!quiet) setRefreshing(true);
    try {
      const [dash, list, places, history] = await Promise.all([
        api<Dashboard>("/api/dashboard", {}, "dashboard"),
        api<Beverage[]>("/api/references?limit=500", {}, "beverages"),
        api<Location[]>("/api/locations", {}, "locations"),
        api<EventItem[]>("/api/events", {}, "events"),
      ]);
      const cols = await api<Collection[]>("/api/collections", {}, "collections");
      setDashboard(dash);
      setBeverages(list);
      setLocations(places);
      setEvents(history);
      setCollections(cols);
    } catch (error) {
      if (online) notify(messageFrom(error), "error");
    } finally {
      setRefreshing(false);
    }
  }, [notify, online]);

  useEffect(() => {
    (async () => {
      const cachedUser = localStorage.getItem(USER_CACHE_KEY);
      if (getToken() && cachedUser) {
        try { setUser(JSON.parse(cachedUser) as User); } catch { /* ignore invalid cache */ }
      }
      try {
        const status = await api<{ needs_setup: boolean }>("/api/auth/setup-status");
        setNeedsSetup(status.needs_setup);
        if (getToken() && !status.needs_setup) {
          const me = await api<User>("/api/auth/me");
          setUser(me);
          localStorage.setItem(USER_CACHE_KEY, JSON.stringify(me));
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 401 && navigator.onLine) {
          setToken(null);
          localStorage.removeItem(USER_CACHE_KEY);
          setUser(null);
        }
        // The cached PWA can still open offline when a previous session exists.
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (user) {
      loadData();
      refreshQueueCount();
    }
  }, [user, loadData, refreshQueueCount]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [view, user]);

  useEffect(() => {
    const handleOnline = async () => {
      setOnline(true);
      try {
        const result = await syncQueue();
        if (result.applied) notify(`${result.applied} action(s) hors ligne synchronisée(s)`);
        if (result.rejected) notify(`${result.rejected} action(s) nécessite(nt) une vérification`, "error");
        await refreshQueueCount();
        await loadData(true);
      } catch { /* retry on the next connectivity event */ }
    };
    const handleOffline = () => setOnline(false);
    addEventListener("online", handleOnline);
    addEventListener("offline", handleOffline);
    addEventListener("cellier:queue-changed", refreshQueueCount);
    return () => {
      removeEventListener("online", handleOnline);
      removeEventListener("offline", handleOffline);
      removeEventListener("cellier:queue-changed", refreshQueueCount);
    };
  }, [loadData, notify, refreshQueueCount]);

  useEffect(() => {
    const unauthorized = async () => {
      if (!navigator.onLine) return;
      setToken(null);
      localStorage.removeItem(USER_CACHE_KEY);
      await clearLocalData();
      setUser(null);
      notify("Votre session a expiré, veuillez vous reconnecter", "info");
    };
    addEventListener("cellier:unauthorized", unauthorized);
    return () => removeEventListener("cellier:unauthorized", unauthorized);
  }, [notify]);

  const filtered = useMemo(() => {
    const needle = normalize(search);
    return beverages.filter((item) => {
      if (collectionFilter && item.collection_id !== collectionFilter) return false;
      if (!needle) return true;
      const haystack = normalize([
        item.name, item.producer, item.category, item.region, item.country,
        item.barcode, ...item.tags,
        ...item.variants.flatMap((v) => [v.vintage, ...v.positions.map((p) => p.location_path)]),
      ].filter(Boolean).join(" "));
      if (haystack.includes(needle)) return true;
      return needle.split(/\s+/).every((word) => haystack.split(/\s+/).some((candidate) => candidate.startsWith(word)));
    });
  }, [beverages, collectionFilter, search]);

  async function finishAction(message: string) {
    setActionMode(null);
    setSelected(null);
    notify(message, message.includes("hors ligne") ? "info" : "success");
    await refreshQueueCount();
    if (online) await loadData(true);
  }

  async function undoEvent(event: EventItem) {
    try {
      await api(`/api/events/${event.id}/undo`, { method: "POST" });
      notify("Action annulée");
      await loadData(true);
    } catch (error) {
      notify(messageFrom(error), "error");
    }
  }

  async function disablePartyMode() {
    if (partyChanging) return;
    setPartyChanging(true);
    try {
      await api("/api/settings/party-mode", {
        method: "PUT",
        body: JSON.stringify({ enabled: false }),
      });
      setDashboard((current) => current ? { ...current, party_mode: false } : current);
      if (view === "more") setView("home");
      notify("Mode soirée désactivé");
      await loadData(true);
    } catch (error) {
      notify(messageFrom(error), "error");
    } finally {
      setPartyChanging(false);
    }
  }

  function selectById(id: number) {
    const item = beverages.find((value) => value.id === id);
    if (item) setSelected(item);
  }

  async function logout() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* local logout still works */ }
    setToken(null);
    localStorage.removeItem(USER_CACHE_KEY);
    await clearLocalData();
    setUser(null);
    setDashboard(null);
  }

  if (booting) return <div className="splash"><div className="brand-mark"><Bottle /></div><LoaderCircle className="spin" /></div>;
  if (!user) return <AuthScreen needsSetup={needsSetup} onAuthenticated={setUser} />;

  const party = dashboard?.party_mode;
  return (
    <div className={`app-shell ${party ? "party-mode" : ""}`}>
      <div className={`network-banner ${online ? "online" : "offline"}`}>
        {online ? <Wifi size={14} /> : <WifiOff size={14} />}
        <span>{online ? (pending ? `${pending} action(s) à synchroniser` : "Synchronisé") : `Hors ligne${pending ? ` · ${pending} en attente` : ""}`}</span>
      </div>
      {party && <div className="party-mode-bar" role="status" aria-live="polite">
        <div><Sparkles size={18} /><strong>Mode soirée actif</strong></div>
        <button type="button" onClick={disablePartyMode} disabled={partyChanging}>
          {partyChanging ? <LoaderCircle className="spin" /> : <X />}
          {partyChanging ? "Désactivation…" : "Quitter"}
        </button>
      </div>}
      <aside className="desktop-sidebar">
        <div className="desktop-brand"><div className="brand-mark"><Bottle /></div><strong>Cellier</strong></div>
        <nav>
          <NavItem icon={<Home />} label="Accueil" active={view === "home"} onClick={() => setView("home")} />
          <NavItem icon={<Wine />} label="Collections" active={view === "inventory"} onClick={() => setView("inventory")} />
          <NavItem icon={<Camera />} label="Scanner" active={view === "scan"} onClick={() => setView("scan")} accent />
          <NavItem icon={<Heart />} label="Journal" active={view === "journal"} onClick={() => setView("journal")} />
          {!party && <NavItem icon={<MoreHorizontal />} label="Plus" active={view === "more"} onClick={() => setView("more")} />}
        </nav>
        <div className="sidebar-user"><div className="avatar small">{user.display_name[0]}</div><div><strong>{user.display_name}</strong><span>{online ? "En ligne" : "Hors ligne"}</span></div></div>
      </aside>
      <main className="main-content">
        {view === "home" && <div className="view home-view">
          <header className="home-header">
            <div><p className="eyebrow">{party ? "MODE SOIRÉE" : "BONJOUR"}</p><h1>{user.display_name}</h1><p>{party ? "Les actions essentielles, rien de plus." : "Qu’allons-nous ouvrir aujourd’hui ?"}</p></div>
            <IconButton label="Actualiser" onClick={() => loadData()} className={refreshing ? "spin" : ""}><RefreshCw /></IconButton>
          </header>
          <button className="scan-hero" onClick={() => setView("scan")}>
            <div className="scan-icon"><Camera /></div>
            <div><strong>Scanner une boisson</strong><span>Ajouter, identifier ou retirer</span></div>
            <ChevronRight />
          </button>
          <section className="quick-grid">
            <button onClick={() => { setView("inventory"); setSearch(""); }}><Search /><strong>Trouver</strong><span>Dans tout le stock</span></button>
            <button onClick={() => { setView("inventory"); setSearch(""); }}><Minus /><strong>Retirer</strong><span>En quelques secondes</span></button>
            <button onClick={() => { setView("inventory"); setSearch(""); }}><ArrowLeftRight /><strong>Déplacer</strong><span>Vers un autre lieu</span></button>
          </section>
          <section className="stats-strip">
            <div><strong>{dashboard?.total_quantity ?? "—"}</strong><span>boissons</span></div>
            <div><strong>{dashboard?.open_containers ?? "—"}</strong><span>ouvertes</span></div>
            <div><strong>{dashboard?.reservations ?? "—"}</strong><span>réservations</span></div>
          </section>
          {!party && <section className="home-section">
            <div className="section-title"><div><p className="eyebrow">À PORTÉE DE MAIN</p><h2>Dernières références</h2></div><button onClick={() => setView("inventory")}>Tout voir <ChevronRight /></button></div>
            <div className="beverage-list">
              {beverages.filter((x) => x.quantity > 0).slice(0, 4).map((item) => <BeverageCard key={item.id} item={item} onClick={() => setSelected(item)} />)}
              {!beverages.length && <EmptyState icon={<Bottle />} title="Votre cellier est prêt" text="Ajoutez votre première boisson pour commencer." action={<button className="button primary" onClick={() => setAdding(true)}><Plus /> Ajouter</button>} />}
            </div>
          </section>}
        </div>}
        {view === "inventory" && <div className="view">
          <header className="view-header inventory-heading">
            <div><p className="eyebrow">TOUT LE STOCK</p><h1>Collections</h1><p>{filtered.reduce((sum, x) => sum + x.quantity, 0)} unités affichées</p></div>
            {has(user, "reference:add") && <button className="button primary" onClick={() => setAdding(true)}><Plus /> Ajouter</button>}
          </header>
          <div className="search-box"><Search /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nom, producteur, année, lieu…" />{search && <button onClick={() => setSearch("")}><X /></button>}</div>
          <div className="collection-chips">
            <button className={collectionFilter === null ? "active" : ""} onClick={() => setCollectionFilter(null)}>Toutes <span>{beverages.length}</span></button>
            {collections.map((collection) => <button className={collectionFilter === collection.id ? "active" : ""} key={collection.id} onClick={() => setCollectionFilter(collection.id)}>{collection.name} <span>{collection.references}</span></button>)}
          </div>
          <div className="beverage-list">
            {filtered.map((item) => <BeverageCard key={item.id} item={item} onClick={() => setSelected(item)} />)}
            {!filtered.length && <EmptyState icon={<Search />} title="Aucun résultat" text="Essayez un nom incomplet, un producteur, une année ou un emplacement." />}
          </div>
        </div>}
        {view === "scan" && <ScanView onAdd={() => setAdding(true)} onFound={selectById} />}
        {view === "journal" && <div className="view">
          <header className="view-header"><p className="eyebrow">TRAÇABILITÉ</p><h1>Journal</h1><p>Chaque mouvement reste explicable et, lorsque possible, annulable.</p></header>
          <div className="timeline">
            {events.map((event) => <article className={event.undone ? "undone" : ""} key={event.id}>
              <div className={`timeline-icon ${event.event_type}`}>{event.event_type === "add" ? <Plus /> : event.event_type === "move" ? <ArrowLeftRight /> : event.event_type === "undo" ? <RotateCcw /> : <Minus />}</div>
              <div><strong>{eventLabels[event.event_type] || event.event_type}</strong><span>{event.quantity ? `${event.quantity} unité(s)` : ""}</span><small>{new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(event.created_at))}</small></div>
              {event.can_undo && has(user, "stock:correct") && <button onClick={() => undoEvent(event)}><RotateCcw /> Annuler</button>}
            </article>)}
            {!events.length && <EmptyState icon={<RefreshCw />} title="Aucun mouvement" text="Les ajouts, retraits et déplacements apparaîtront ici." />}
          </div>
        </div>}
        {view === "more" && !party && <MoreView user={user} locations={locations} collections={collections} dashboard={dashboard} onRefresh={() => loadData(true)} notify={notify} logout={logout} />}
      </main>
      <nav className="bottom-nav">
        <NavItem icon={<Home />} label="Accueil" active={view === "home"} onClick={() => setView("home")} />
        <NavItem icon={<Wine />} label="Collections" active={view === "inventory"} onClick={() => setView("inventory")} />
        <NavItem icon={<Camera />} label="Scanner" active={view === "scan"} onClick={() => setView("scan")} accent />
        <NavItem icon={<Heart />} label="Journal" active={view === "journal"} onClick={() => setView("journal")} />
        {!party && <NavItem icon={<MoreHorizontal />} label="Plus" active={view === "more"} onClick={() => setView("more")} />}
      </nav>
      {selected && !actionMode && <BeverageDetail item={selected} user={user} onClose={() => setSelected(null)} onAction={setActionMode} />}
      {selected && actionMode && <ActionSheet item={selected} mode={actionMode} locations={locations} onClose={() => setActionMode(null)} onDone={finishAction} />}
      {adding && <AddReferenceSheet collections={collections} locations={locations} onClose={() => setAdding(false)} onCreated={async (message) => { setAdding(false); notify(message, message.includes("hors ligne") ? "info" : "success"); if (online) await loadData(true); }} />}
      <div className="toast-stack">{toasts.map((toast) => <div key={toast.id} className={`toast ${toast.tone}`}>{toast.tone === "success" ? <Check /> : toast.tone === "error" ? <X /> : <CloudOff />}<span>{toast.message}</span></div>)}</div>
    </div>
  );
}

function NavItem({
  icon,
  label,
  active,
  onClick,
  accent = false,
}: {
  icon: ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  accent?: boolean;
}) {
  return <button className={`${active ? "active" : ""} ${accent ? "accent" : ""}`} onClick={onClick}><span>{icon}</span><small>{label}</small></button>;
}
