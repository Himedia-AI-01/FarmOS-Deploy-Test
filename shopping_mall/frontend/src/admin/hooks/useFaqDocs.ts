import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import type {
  FaqDoc,
  FaqDocCreate,
  FaqDocUpdate,
  FaqCategory,
  FaqCategoryCreate,
  FaqCategoryUpdate,
} from '@/admin/types/faq';

// ──────────────────────────────────────────
// Query key factory
// ──────────────────────────────────────────

const KEYS = {
  categories: ['admin-faq-categories'] as const,
  categoryList: (includeInactive: boolean) =>
    ['admin-faq-categories', 'list', { includeInactive }] as const,
  docs: ['admin-faq-docs'] as const,
  list: (filters: FaqListFilters) => ['admin-faq-docs', 'list', filters] as const,
  detail: (id: number) => ['admin-faq-docs', 'detail', id] as const,
};

// ──────────────────────────────────────────
// Types
// ──────────────────────────────────────────

export interface FaqListFilters {
  faq_category_id?: number | null;
  is_active?: boolean | 'all';
  include_analytics?: boolean;
  limit?: number;
  offset?: number;
}

// ──────────────────────────────────────────
// FAQ Category Hooks
// ──────────────────────────────────────────

export function useFaqCategories(includeInactive = false) {
  return useQuery<FaqCategory[]>({
    queryKey: KEYS.categoryList(includeInactive),
    queryFn: async () => {
      const { data } = await api.get('/api/admin/faq-categories', {
        params: includeInactive ? { include_inactive: true } : {},
      });
      return data;
    },
  });
}

export function useCreateFaqCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: FaqCategoryCreate) => {
      const { data } = await api.post('/api/admin/faq-categories', payload);
      return data as FaqCategory;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}

export function useUpdateFaqCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: FaqCategoryUpdate }) => {
      const { data } = await api.put(`/api/admin/faq-categories/${id}`, payload);
      return data as FaqCategory;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}

export function useDeleteFaqCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, force = false }: { id: number; force?: boolean }) => {
      await api.delete(`/api/admin/faq-categories/${id}`, { params: { force } });
      return id;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
      queryClient.invalidateQueries({ queryKey: KEYS.docs });
    },
  });
}

// ──────────────────────────────────────────
// FAQ Doc Hooks
// ──────────────────────────────────────────

export function useFaqDocs(filters: FaqListFilters = {}) {
  const params: Record<string, string> = {};
  if (filters.faq_category_id != null) params.faq_category_id = String(filters.faq_category_id);
  if (filters.is_active !== undefined && filters.is_active !== 'all')
    params.is_active = String(filters.is_active);
  if (filters.include_analytics === false) params.include_analytics = 'false';
  if (filters.limit != null) params.limit = String(filters.limit);
  if (filters.offset != null) params.offset = String(filters.offset);

  return useQuery<FaqDoc[]>({
    queryKey: KEYS.list(filters),
    queryFn: async () => {
      const { data } = await api.get('/api/admin/faq-docs', { params });
      return data;
    },
  });
}

export function useFaqDoc(id: number | null) {
  return useQuery<FaqDoc>({
    queryKey: KEYS.detail(id!),
    queryFn: async () => {
      const { data } = await api.get(`/api/admin/faq-docs/${id}`);
      return data;
    },
    enabled: id != null,
  });
}

export function useCreateFaqDoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: FaqDocCreate) => {
      const { data } = await api.post('/api/admin/faq-docs', payload);
      return data as FaqDoc;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KEYS.docs });
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}

export function useUpdateFaqDoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: FaqDocUpdate }) => {
      const { data } = await api.put(`/api/admin/faq-docs/${id}`, payload);
      return data as FaqDoc;
    },
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: KEYS.docs });
      queryClient.invalidateQueries({ queryKey: KEYS.detail(id) });
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}

export function useDeleteFaqDoc() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/api/admin/faq-docs/${id}`);
      return id;
    },
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: KEYS.docs });
      queryClient.invalidateQueries({ queryKey: KEYS.detail(id) });
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}

export function useToggleFaqActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, is_active }: { id: number; is_active: boolean }) => {
      const { data } = await api.put(`/api/admin/faq-docs/${id}`, { is_active });
      return data as FaqDoc;
    },
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: KEYS.docs });
      queryClient.invalidateQueries({ queryKey: KEYS.detail(id) });
      queryClient.invalidateQueries({ queryKey: KEYS.categories });
    },
  });
}
