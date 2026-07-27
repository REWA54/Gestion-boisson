export type User = {
  id: number;
  username: string;
  display_name: string;
  role: string;
  permissions: Record<string, boolean>;
  is_admin: boolean;
};

export type Collection = {
  id: number;
  name: string;
  description: string;
  icon: string;
  quantity: number;
  references: number;
};

export type Location = {
  id: number;
  collection_id: number;
  parent_id: number | null;
  name: string;
  kind: string;
  qr_code: string | null;
  is_terminal: boolean;
  path: string;
  occupied: boolean;
};

export type Position = {
  id: number;
  location_id: number;
  location_name: string;
  location_path: string;
  quantity: number;
  reserved_quantity: number;
  packaging: string;
  units_per_package: number;
  closed_packages: number;
  package_state: string;
};

export type Variant = {
  id: number;
  vintage: string;
  volume_ml: number | null;
  batch: string;
  format: string;
  edition: string;
  alcohol_percent: number | null;
  quantity: number;
  reserved_quantity: number;
  open_containers: number;
  positions: Position[];
};

export type Beverage = {
  id: number;
  collection_id: number;
  name: string;
  producer: string;
  category: string;
  subcategory: string;
  country: string;
  region: string;
  description: string;
  photo_path: string | null;
  barcode: string | null;
  alcohol_percent: number | null;
  tags: string[];
  variants: Variant[];
  quantity: number;
};

export type Dashboard = {
  greeting_name: string;
  total_quantity: number;
  reference_count: number;
  open_containers: number;
  reservations: number;
  party_mode: boolean;
  pending_sync: number;
};

export type EventItem = {
  id: number;
  event_type: string;
  variant_id: number | null;
  source_location_id: number | null;
  target_location_id: number | null;
  quantity: number;
  created_at: string;
  undone: boolean;
  can_undo: boolean;
};
