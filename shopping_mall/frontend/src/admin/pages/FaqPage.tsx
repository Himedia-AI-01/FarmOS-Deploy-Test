import { useState, useMemo } from 'react';
import {
  useFaqCategories,
  useCreateFaqCategory,
  useUpdateFaqCategory,
  useDeleteFaqCategory,
  useFaqDocs,
  useCreateFaqDoc,
  useUpdateFaqDoc,
  useDeleteFaqDoc,
  useToggleFaqActive,
} from '@/admin/hooks/useFaqDocs';
import type {
  FaqCategory,
  FaqCategoryCreate,
  FaqCategoryUpdate,
  FaqDoc,
  FaqDocCreate,
  FaqDocUpdate,
} from '@/admin/types/faq';
import { PRESET_COLORS, PRESET_ICONS } from '@/admin/types/faq';
import { formatDate } from '@/lib/utils';

// ──────────────────────────────────────────
// Sidebar filter type
// ──────────────────────────────────────────

type SidebarSelection =
  | { type: 'all' }
  | { type: 'uncategorized' }
  | { type: 'category'; id: number };

type SortKey = 'recent' | 'citations';

// ──────────────────────────────────────────
// CategoryFormModal
// ──────────────────────────────────────────

interface CategoryFormModalProps {
  category: FaqCategory | null; // null = create
  onClose: () => void;
}

function CategoryFormModal({ category, onClose }: CategoryFormModalProps) {
  const isEdit = category != null;

  const [name, setName] = useState(category?.name ?? '');
  const [slug, setSlug] = useState(category?.slug ?? '');
  const [description, setDescription] = useState(category?.description ?? '');
  const [color, setColor] = useState(category?.color ?? PRESET_COLORS[0].value);
  const [icon, setIcon] = useState(category?.icon ?? PRESET_ICONS[0].value);
  const [slugError, setSlugError] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const { mutate: create, isPending: isCreating } = useCreateFaqCategory();
  const { mutate: update, isPending: isUpdating } = useUpdateFaqCategory();
  const isPending = isCreating || isUpdating;

  function autoSlug(n: string) {
    return n
      .toLowerCase()
      .replace(/\s+/g, '-')
      .replace(/[^a-z0-9-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
  }

  function handleNameChange(v: string) {
    setName(v);
    if (!isEdit) setSlug(autoSlug(v));
  }

  function validateSlug(v: string) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(v.trim())) {
      setSlugError('영소문자·숫자·하이픈만 사용 가능합니다.');
      return false;
    }
    setSlugError('');
    return true;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    if (!isEdit && !validateSlug(slug)) return;

    if (isEdit) {
      const payload: FaqCategoryUpdate = {
        name: name.trim(),
        description: description.trim() || null,
        color,
        icon,
      };
      update(
        { id: category.id, payload },
        {
          onSuccess: () => {
            setFeedback({ type: 'success', msg: '수정됐습니다.' });
            setTimeout(onClose, 1000);
          },
          onError: () => {
            setFeedback({ type: 'error', msg: '수정에 실패했습니다.' });
            setTimeout(() => setFeedback(null), 3000);
          },
        },
      );
    } else {
      const payload: FaqCategoryCreate = {
        name: name.trim(),
        slug,
        description: description.trim() || null,
        color,
        icon,
      };
      create(payload, {
        onSuccess: () => {
          setFeedback({ type: 'success', msg: '카테고리를 만들었습니다.' });
          setTimeout(onClose, 1000);
        },
        onError: (err: unknown) => {
          const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          setFeedback({ type: 'error', msg: detail ?? '생성에 실패했습니다.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      });
    }
  }

  // Preview chip
  const previewChip = (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-bold ${color}`}>
      <span className="material-symbols-outlined text-[13px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
        {icon}
      </span>
      {name || '미리보기'}
    </span>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? '카테고리 수정' : '카테고리 추가'}
    >
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-100">
          <h2 className="text-base font-bold text-stone-900">
            {isEdit ? '카테고리 수정' : '카테고리 추가'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-stone-400 hover:text-stone-600 hover:bg-stone-100 transition-colors"
            aria-label="닫기"
          >
            <span className="material-symbols-outlined text-[20px]" aria-hidden="true">close</span>
          </button>
        </div>

        <form id="cat-form" onSubmit={handleSubmit} className="px-6 py-5 space-y-4 overflow-y-auto max-h-[70vh]">
          {feedback && (
            <div
              className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium ${
                feedback.type === 'success'
                  ? 'bg-green-50 border border-green-200 text-green-700'
                  : 'bg-red-50 border border-red-200 text-red-700'
              }`}
              role="status"
            >
              <span className="material-symbols-outlined text-[16px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                {feedback.type === 'success' ? 'check_circle' : 'error'}
              </span>
              {feedback.msg}
            </div>
          )}

          {/* Preview */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-stone-400 font-medium">미리보기</span>
            {previewChip}
          </div>

          {/* Name */}
          <div>
            <label htmlFor="cat-name" className="block text-xs font-semibold text-stone-600 mb-1.5">이름 *</label>
            <input
              id="cat-name"
              type="text"
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              required
              placeholder="예: 배송·교환 안내"
              className="w-full border border-stone-200 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
            />
          </div>

          {/* Slug (create only) */}
          {!isEdit && (
            <div>
              <label htmlFor="cat-slug" className="block text-xs font-semibold text-stone-600 mb-1.5">
                슬러그 *{' '}
                <span className="font-normal text-stone-400">(영소문자·숫자·하이픈)</span>
              </label>
              <input
                id="cat-slug"
                type="text"
                value={slug}
                onChange={(e) => { setSlug(e.target.value); validateSlug(e.target.value); }}
                required
                placeholder="shipping-exchange"
                className={`w-full border rounded-xl px-3 py-2 text-sm outline-none transition-all ${
                  slugError
                    ? 'border-red-300 focus:border-red-400 focus:ring-2 focus:ring-red-400/20'
                    : 'border-stone-200 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20'
                }`}
              />
              {slugError && <p className="text-[11px] text-red-500 mt-1">{slugError}</p>}
            </div>
          )}

          {/* Description */}
          <div>
            <label htmlFor="cat-desc" className="block text-xs font-semibold text-stone-600 mb-1.5">
              설명 <span className="font-normal text-stone-400">(선택)</span>
            </label>
            <input
              id="cat-desc"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="배송 일정, 반품·교환 절차 안내"
              className="w-full border border-stone-200 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
            />
          </div>

          {/* Color picker */}
          <div>
            <p className="text-xs font-semibold text-stone-600 mb-2">색상</p>
            <div className="flex flex-wrap gap-2">
              {PRESET_COLORS.map((pc) => (
                <button
                  key={pc.value}
                  type="button"
                  onClick={() => setColor(pc.value)}
                  className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold border-2 transition-all ${pc.value} ${
                    color === pc.value ? 'border-current scale-105 shadow-sm' : 'border-transparent'
                  }`}
                  title={pc.label}
                >
                  {pc.label}
                </button>
              ))}
            </div>
          </div>

          {/* Icon picker */}
          <div>
            <p className="text-xs font-semibold text-stone-600 mb-2">아이콘</p>
            <div className="flex flex-wrap gap-1.5">
              {PRESET_ICONS.map((pi) => (
                <button
                  key={pi.value}
                  type="button"
                  onClick={() => setIcon(pi.value)}
                  title={pi.label}
                  className={`w-9 h-9 rounded-lg flex items-center justify-center border-2 transition-all ${
                    icon === pi.value
                      ? 'border-emerald-500 bg-emerald-50 text-emerald-700'
                      : 'border-stone-100 text-stone-500 hover:border-stone-200 hover:bg-stone-50'
                  }`}
                >
                  <span className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                    {pi.value}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </form>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-stone-100">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-semibold text-stone-500 hover:bg-stone-100 transition-colors">
            취소
          </button>
          <button
            type="submit"
            form="cat-form"
            disabled={isPending || !name.trim()}
            className="px-4 py-2 rounded-xl text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            {isPending ? '저장 중...' : isEdit ? '저장' : '추가'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// DeleteCategoryModal
// ──────────────────────────────────────────

function DeleteCategoryModal({
  category,
  onClose,
}: {
  category: FaqCategory;
  onClose: () => void;
}) {
  const [force, setForce] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const { mutate: del, isPending } = useDeleteFaqCategory();

  function handleConfirm() {
    del(
      { id: category.id, force },
      {
        onSuccess: onClose,
        onError: (err: unknown) => {
          const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          setFeedback(detail ?? '삭제에 실패했습니다.');
        },
      },
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-red-500 text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
              delete_forever
            </span>
          </div>
          <div>
            <h3 className="text-base font-bold text-stone-900">카테고리 삭제</h3>
            <p className="text-xs text-stone-500 mt-0.5">이 작업은 되돌릴 수 없습니다.</p>
          </div>
        </div>

        <div className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold ${category.color}`}>
          <span className="material-symbols-outlined text-[13px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
            {category.icon}
          </span>
          {category.name}
          <span className="ml-1 opacity-60">({category.doc_count}개 문서)</span>
        </div>

        {category.doc_count > 0 && (
          <label className="flex items-start gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
              className="mt-0.5 rounded border-stone-300 text-red-500 focus:ring-red-400"
            />
            <span className="text-xs text-stone-600">
              연결된 문서 {category.doc_count}개를 <strong>미분류</strong>로 이동 후 삭제
              <br />
              <span className="text-stone-400">(체크 해제 시 삭제 불가)</span>
            </span>
          </label>
        )}

        {feedback && (
          <p className="text-xs text-red-500 bg-red-50 px-3 py-2 rounded-lg border border-red-100">{feedback}</p>
        )}

        <div className="flex gap-3 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 py-2.5 rounded-xl text-sm font-semibold text-stone-500 bg-stone-100 hover:bg-stone-200 transition-colors"
          >
            취소
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={isPending || (category.doc_count > 0 && !force)}
            className="flex-1 py-2.5 rounded-xl text-sm font-semibold text-white bg-red-500 hover:bg-red-600 disabled:opacity-40 transition-colors"
          >
            {isPending ? '삭제 중...' : '삭제'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// DocFormModal
// ──────────────────────────────────────────

interface DocFormModalProps {
  doc: FaqDoc | null;
  defaultCategoryId?: number | null;
  categories: FaqCategory[];
  onClose: () => void;
}

function DocFormModal({ doc, defaultCategoryId, categories, onClose }: DocFormModalProps) {
  const isEdit = doc != null;

  const [categoryId, setCategoryId] = useState<number | null>(
    doc?.faq_category_id ?? defaultCategoryId ?? null,
  );
  const [title, setTitle] = useState(doc?.title ?? '');
  const [content, setContent] = useState(doc?.content ?? '');
  const [extraRaw, setExtraRaw] = useState(
    doc?.extra_metadata ? JSON.stringify(doc.extra_metadata, null, 2) : '',
  );
  const [extraError, setExtraError] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const { mutate: create, isPending: isCreating } = useCreateFaqDoc();
  const { mutate: update, isPending: isUpdating } = useUpdateFaqDoc();
  const isPending = isCreating || isUpdating;

  function parseExtra(): Record<string, unknown> | null | undefined {
    if (!extraRaw.trim()) return null;
    try {
      const parsed = JSON.parse(extraRaw);
      setExtraError('');
      return parsed as Record<string, unknown>;
    } catch {
      setExtraError('유효하지 않은 JSON 형식입니다.');
      return undefined;
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;
    const extra = parseExtra();
    if (extraRaw.trim() && extra === undefined) return;

    if (isEdit) {
      const payload: FaqDocUpdate = {
        faq_category_id: categoryId ?? 0, // 0 → uncategorized on backend
        title: title.trim(),
        content: content.trim(),
        ...(extra !== undefined && { extra_metadata: extra }),
      };
      update(
        { id: doc.id, payload },
        {
          onSuccess: () => {
            setFeedback({ type: 'success', msg: '수정됐습니다.' });
            setTimeout(onClose, 1000);
          },
          onError: () => {
            setFeedback({ type: 'error', msg: '수정에 실패했습니다.' });
            setTimeout(() => setFeedback(null), 3000);
          },
        },
      );
    } else {
      const payload: FaqDocCreate = {
        faq_category_id: categoryId,
        title: title.trim(),
        content: content.trim(),
        ...(extra !== undefined && extra !== null && { extra_metadata: extra }),
      };
      create(payload, {
        onSuccess: () => {
          setFeedback({ type: 'success', msg: '등록됐습니다.' });
          setTimeout(onClose, 1000);
        },
        onError: () => {
          setFeedback({ type: 'error', msg: '등록에 실패했습니다.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      });
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? 'FAQ 문서 수정' : 'FAQ 문서 등록'}
    >
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-100">
          <h2 className="text-base font-bold text-stone-900">{isEdit ? 'FAQ 수정' : 'FAQ 등록'}</h2>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg text-stone-400 hover:bg-stone-100 transition-colors" aria-label="닫기">
            <span className="material-symbols-outlined text-[20px]" aria-hidden="true">close</span>
          </button>
        </div>

        <form id="doc-form" onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {feedback && (
            <div
              className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium ${
                feedback.type === 'success'
                  ? 'bg-green-50 border border-green-200 text-green-700'
                  : 'bg-red-50 border border-red-200 text-red-700'
              }`}
              role="status"
            >
              <span className="material-symbols-outlined text-[16px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                {feedback.type === 'success' ? 'check_circle' : 'error'}
              </span>
              {feedback.msg}
            </div>
          )}

          {/* Category selector */}
          <div>
            <label className="block text-xs font-semibold text-stone-600 mb-2">카테고리</label>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setCategoryId(null)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold border-2 transition-all ${
                  categoryId === null
                    ? 'border-stone-400 bg-stone-100 text-stone-700'
                    : 'border-stone-100 text-stone-400 hover:border-stone-200'
                }`}
              >
                미분류
              </button>
              {categories.map((cat) => (
                <button
                  key={cat.id}
                  type="button"
                  onClick={() => setCategoryId(cat.id)}
                  className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold border-2 transition-all ${
                    categoryId === cat.id
                      ? `${cat.color} border-current scale-105`
                      : `border-transparent ${cat.color} opacity-60 hover:opacity-90`
                  }`}
                >
                  <span className="material-symbols-outlined text-[13px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                    {cat.icon}
                  </span>
                  {cat.name}
                </button>
              ))}
            </div>
          </div>

          {/* Title */}
          <div>
            <label htmlFor="doc-title" className="block text-xs font-semibold text-stone-600 mb-1.5">질문 *</label>
            <input
              id="doc-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              maxLength={500}
              placeholder="예: 배송은 얼마나 걸리나요?"
              className="w-full border border-stone-200 rounded-xl px-4 py-2.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
            />
            <p className="text-xs text-stone-400 text-right mt-1">{title.length}/500</p>
          </div>

          {/* Content */}
          <div>
            <label htmlFor="doc-content" className="block text-xs font-semibold text-stone-600 mb-1.5">답변 *</label>
            <textarea
              id="doc-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              required
              rows={7}
              placeholder="예: 평균 2~3일 내 배송됩니다. 산간 지역은 추가 1~2일이 소요될 수 있습니다."
              className="w-full border border-stone-200 rounded-xl px-4 py-2.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all resize-y"
            />
          </div>

          {/* Extra metadata */}
          <div>
            <label htmlFor="doc-extra" className="block text-xs font-semibold text-stone-600 mb-1.5">
              추가 메타데이터 <span className="font-normal text-stone-400">(선택 · JSON)</span>
            </label>
            <textarea
              id="doc-extra"
              value={extraRaw}
              onChange={(e) => { setExtraRaw(e.target.value); setExtraError(''); }}
              onBlur={() => {
                if (extraRaw.trim()) {
                  try {
                    JSON.parse(extraRaw);
                    setExtraError('');
                  } catch {
                    setExtraError('유효하지 않은 JSON 형식입니다.');
                  }
                }
              }}
              rows={2}
              placeholder='{"tags": ["배송", "택배"]}'
              className={`w-full border rounded-xl px-4 py-2.5 text-xs font-mono outline-none transition-all resize-none ${
                extraError
                  ? 'border-red-300 focus:border-red-400 focus:ring-2 focus:ring-red-400/20'
                  : 'border-stone-200 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20'
              }`}
            />
            {extraError && <p className="text-[11px] text-red-500 mt-1">{extraError}</p>}
          </div>
        </form>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-stone-100">
          <button type="button" onClick={onClose} className="px-5 py-2 rounded-xl text-sm font-semibold text-stone-500 hover:bg-stone-100 transition-colors">
            취소
          </button>
          <button
            type="submit"
            form="doc-form"
            disabled={isPending || !title.trim() || !content.trim()}
            className="px-5 py-2 rounded-xl text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            {isPending ? (isEdit ? '저장 중...' : '등록 중...') : isEdit ? '저장' : '등록'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// DeleteDocModal
// ──────────────────────────────────────────

function DeleteDocModal({
  doc,
  onClose,
  onConfirm,
  isPending,
}: {
  doc: FaqDoc;
  onClose: () => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4" role="dialog" aria-modal="true">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-red-500 text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
              delete_forever
            </span>
          </div>
          <div>
            <h3 className="text-base font-bold text-stone-900">문서 삭제</h3>
            <p className="text-xs text-stone-500 mt-0.5">ChromaDB 인덱스에서도 즉시 제거됩니다.</p>
          </div>
        </div>
        <p className="text-sm text-stone-700 font-medium line-clamp-2 bg-stone-50 rounded-xl px-4 py-3">{doc.title}</p>
        <div className="flex gap-3">
          <button type="button" onClick={onClose} className="flex-1 py-2.5 rounded-xl text-sm font-semibold text-stone-500 bg-stone-100 hover:bg-stone-200 transition-colors">
            취소
          </button>
          <button type="button" onClick={onConfirm} disabled={isPending} className="flex-1 py-2.5 rounded-xl text-sm font-semibold text-white bg-red-500 hover:bg-red-600 disabled:opacity-50 transition-colors">
            {isPending ? '삭제 중...' : '삭제'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// FaqPage
// ──────────────────────────────────────────

export default function FaqPage() {
  // ── Category sidebar state ──
  const [selected, setSelected] = useState<SidebarSelection>({ type: 'all' });
  const [catFormTarget, setCatFormTarget] = useState<FaqCategory | 'new' | null>(null);
  const [deleteCatTarget, setDeleteCatTarget] = useState<FaqCategory | null>(null);

  // ── Doc table state ──
  const [sortKey, setSortKey] = useState<SortKey>('recent');
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState<'all' | 'active' | 'inactive'>('active');
  const [editDoc, setEditDoc] = useState<FaqDoc | null>(null);
  const [isCreatingDoc, setIsCreatingDoc] = useState(false);
  const [deleteDocTarget, setDeleteDocTarget] = useState<FaqDoc | null>(null);
  const [docListError, setDocListError] = useState<string | null>(null);

  // ── Data ──
  const { data: categories = [] } = useFaqCategories(true);

  const docFilters = useMemo(() => {
    const f: Record<string, unknown> = {};
    if (selected.type === 'category') f.faq_category_id = selected.id;
    if (activeFilter !== 'all') f.is_active = activeFilter === 'active';
    return f;
  }, [selected, activeFilter]);

  const { data: allDocs = [], isLoading } = useFaqDocs(docFilters);

  const { mutate: deleteDoc, isPending: isDeleting } = useDeleteFaqDoc();
  const { mutate: toggleActive } = useToggleFaqActive();

  // ── Client-side filtering & sorting ──
  const visibleDocs = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();

    let filtered = allDocs.filter((d) => {
      if (selected.type === 'uncategorized' && d.faq_category_id != null) return false;
      if (q && !d.title.toLowerCase().includes(q) && !d.content.toLowerCase().includes(q)) return false;
      return true;
    });

    if (sortKey === 'citations') {
      filtered = [...filtered].sort((a, b) => b.citation_count - a.citation_count);
    }
    return filtered;
  }, [allDocs, selected, searchQuery, sortKey]);

  // ── Category sidebar helpers ──
  const totalActive = allDocs.filter((d) => d.is_active).length;
  const uncategorizedCount = categories.length > 0
    ? allDocs.filter((d) => d.faq_category_id == null).length
    : 0;

  function handleToggleActive(doc: FaqDoc) {
    toggleActive(
      { id: doc.id, is_active: !doc.is_active },
      {
        onError: (err: unknown) => {
          console.error('[FaqPage] toggleActive 실패', err);
          setDocListError('활성 상태 변경에 실패했습니다.');
          setTimeout(() => setDocListError(null), 3000);
        },
      },
    );
  }

  function handleDeleteDoc() {
    if (!deleteDocTarget) return;
    deleteDoc(deleteDocTarget.id, {
      onSuccess: () => setDeleteDocTarget(null),
      onError: (err: unknown) => {
        console.error('[FaqPage] deleteDoc 실패', err);
        setDocListError('문서 삭제에 실패했습니다.');
        setTimeout(() => setDocListError(null), 3000);
        // deleteDocTarget 유지 — 삭제 확인 모달을 닫지 않음
      },
    });
  }

  function getCategoryChip(doc: FaqDoc) {
    if (!doc.faq_category_id) {
      return (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-stone-100 text-stone-400">
          미분류
        </span>
      );
    }
    const cat = categories.find((c) => c.id === doc.faq_category_id);
    if (!cat) return null;
    return (
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${cat.color}`}>
        <span className="material-symbols-outlined text-[11px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
          {cat.icon}
        </span>
        {cat.name}
      </span>
    );
  }

  const activeCategories = categories.filter((c) => c.is_active);

  return (
    <div className="flex h-full min-h-0 bg-stone-50">
      {/* ──────────────── LEFT SIDEBAR ──────────────── */}
      <aside className="w-56 shrink-0 bg-white border-r border-stone-100 flex flex-col overflow-y-auto">
        {/* Sidebar header */}
        <div className="px-4 pt-5 pb-3 flex items-center justify-between">
          <h2 className="text-xs font-bold text-stone-500 uppercase tracking-wider">카테고리</h2>
          <button
            type="button"
            onClick={() => setCatFormTarget('new')}
            title="카테고리 추가"
            className="w-6 h-6 rounded-md flex items-center justify-center text-stone-400 hover:text-emerald-600 hover:bg-stone-100 transition-colors"
            aria-label="카테고리 추가"
          >
            <span className="material-symbols-outlined text-[16px]" aria-hidden="true">add</span>
          </button>
        </div>

        <nav className="flex-1 px-2 pb-4 space-y-0.5">
          {/* 전체 */}
          <button
            type="button"
            onClick={() => setSelected({ type: 'all' })}
            className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
              selected.type === 'all'
                ? 'bg-emerald-50 text-emerald-700 font-semibold'
                : 'text-stone-600 hover:bg-stone-50'
            }`}
          >
            <span className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px]" aria-hidden="true">grid_view</span>
              전체
            </span>
            <span className={`text-xs tabular-nums ${selected.type === 'all' ? 'text-emerald-500' : 'text-stone-400'}`}>
              {totalActive}
            </span>
          </button>

          {/* 미분류 */}
          <button
            type="button"
            onClick={() => setSelected({ type: 'uncategorized' })}
            className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
              selected.type === 'uncategorized'
                ? 'bg-stone-100 text-stone-800 font-semibold'
                : 'text-stone-500 hover:bg-stone-50'
            }`}
          >
            <span className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px]" aria-hidden="true">inbox</span>
              미분류
            </span>
            {uncategorizedCount > 0 && (
              <span className="text-xs tabular-nums text-stone-400">{uncategorizedCount}</span>
            )}
          </button>

          {/* Divider */}
          <div className="my-2 border-t border-stone-100" />

          {/* Category items */}
          {activeCategories.map((cat) => (
            <div key={cat.id} className="group relative">
              <button
                type="button"
                onClick={() => setSelected({ type: 'category', id: cat.id })}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                  selected.type === 'category' && selected.id === cat.id
                    ? `${cat.color} font-semibold`
                    : 'text-stone-600 hover:bg-stone-50'
                }`}
              >
                <span className="material-symbols-outlined text-[16px] shrink-0" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  {cat.icon}
                </span>
                <span className="flex-1 text-left truncate">{cat.name}</span>
                <span className="text-xs tabular-nums opacity-60 group-hover:opacity-0 transition-opacity">
                  {cat.doc_count}
                </span>
              </button>
              {/* Hover actions */}
              <div className="absolute right-2 top-1/2 -translate-y-1/2 hidden group-hover:flex group-focus-within:flex items-center gap-0.5">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setCatFormTarget(cat); }}
                  title="수정"
                  className="w-5 h-5 rounded flex items-center justify-center text-stone-400 hover:text-emerald-600 hover:bg-white transition-colors"
                  aria-label={`${cat.name} 수정`}
                >
                  <span className="material-symbols-outlined text-[13px]" aria-hidden="true">edit</span>
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setDeleteCatTarget(cat); }}
                  title="삭제"
                  className="w-5 h-5 rounded flex items-center justify-center text-stone-400 hover:text-red-500 hover:bg-white transition-colors"
                  aria-label={`${cat.name} 삭제`}
                >
                  <span className="material-symbols-outlined text-[13px]" aria-hidden="true">delete</span>
                </button>
              </div>
            </div>
          ))}

          {activeCategories.length === 0 && (
            <p className="px-3 py-2 text-xs text-stone-400">카테고리가 없습니다.</p>
          )}
        </nav>
      </aside>

      {/* ──────────────── MAIN AREA ──────────────── */}
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="bg-white border-b border-stone-100 px-6 py-4 flex items-center gap-3 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-[17px] pointer-events-none" aria-hidden="true">
              search
            </span>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="질문·답변 검색..."
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-stone-200 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
              aria-label="FAQ 검색"
            />
          </div>

          {/* Active filter */}
          <div className="flex gap-1 bg-stone-100 rounded-lg p-1">
            {(['active', 'inactive', 'all'] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setActiveFilter(f)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                  activeFilter === f ? 'bg-white text-stone-700 shadow-sm' : 'text-stone-500 hover:text-stone-700'
                }`}
              >
                {f === 'active' ? '활성' : f === 'inactive' ? '비활성' : '전체'}
              </button>
            ))}
          </div>

          {/* Sort */}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="px-3 py-2 rounded-lg border border-stone-200 text-xs text-stone-600 bg-white outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
            aria-label="정렬 기준"
          >
            <option value="recent">최근 등록순</option>
            <option value="citations">많이 인용된 순</option>
          </select>

          <button
            type="button"
            onClick={() => setIsCreatingDoc(true)}
            className="ml-auto flex items-center gap-1.5 px-4 py-2 bg-emerald-600 text-white text-sm font-semibold rounded-xl hover:bg-emerald-700 transition-colors shadow-sm"
          >
            <span className="material-symbols-outlined text-[17px]" aria-hidden="true">add</span>
            FAQ 등록
          </button>
        </div>

        {/* Doc operation error banner */}
        {docListError && (
          <div className="mx-6 mt-4 px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700 font-medium" role="alert">
            {docListError}
          </div>
        )}

        {/* Table */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-12 bg-white rounded-lg border border-stone-100 animate-pulse" aria-hidden="true" />
              ))}
            </div>
          ) : visibleDocs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center space-y-3">
              <span className="material-symbols-outlined text-stone-200 text-[56px]" aria-hidden="true">help_outline</span>
              <p className="text-sm font-medium text-stone-400">
                {searchQuery.trim() ? `"${searchQuery}"에 해당하는 FAQ가 없습니다` : 'FAQ가 없습니다'}
              </p>
              {!searchQuery.trim() && (
                <button
                  type="button"
                  onClick={() => setIsCreatingDoc(true)}
                  className="text-sm text-emerald-600 font-semibold hover:underline"
                >
                  첫 FAQ 등록하기
                </button>
              )}
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-stone-100 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-stone-100 bg-stone-50">
                    <th className="px-4 py-3 text-left text-xs font-semibold text-stone-500 w-8">#</th>
                    <th className="px-3 py-3 text-left text-xs font-semibold text-stone-500 w-28">카테고리</th>
                    <th className="px-3 py-3 text-left text-xs font-semibold text-stone-500">질문</th>
                    <th className="px-3 py-3 text-center text-xs font-semibold text-stone-500 w-16">인용</th>
                    <th className="px-3 py-3 text-center text-xs font-semibold text-stone-500 w-20">수정일</th>
                    <th className="px-3 py-3 w-20" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-50">
                  {visibleDocs.map((doc) => {
                    return (
                      <tr
                        key={doc.id}
                        className={`group transition-colors hover:bg-stone-50 ${!doc.is_active ? 'opacity-40' : ''}`}
                      >
                        <td className="px-4 py-3 text-xs text-stone-300 tabular-nums">{doc.id}</td>
                        <td className="px-3 py-3">{getCategoryChip(doc)}</td>
                        <td className="px-3 py-3 max-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-stone-800 truncate">{doc.title}</span>
                          </div>
                          <p className="text-xs text-stone-400 mt-0.5 truncate">{doc.content}</p>
                        </td>
                        <td className="px-3 py-3 text-center">
                          {doc.citation_count > 0 ? (
                            <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-violet-600 bg-violet-50 px-2 py-0.5 rounded-full tabular-nums">
                              <span className="material-symbols-outlined text-[11px]" aria-hidden="true">format_quote</span>
                              {doc.citation_count}
                            </span>
                          ) : (
                            <span className="text-stone-300 text-xs">-</span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-center text-xs text-stone-400 whitespace-nowrap">
                          {formatDate(doc.updated_at)}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
                            <button
                              type="button"
                              onClick={() => handleToggleActive(doc)}
                              title={doc.is_active ? '비활성화' : '활성화'}
                              className="p-1.5 rounded-lg text-stone-400 hover:text-emerald-600 hover:bg-stone-100 transition-colors"
                              aria-label={doc.is_active ? '비활성화' : '활성화'}
                            >
                              <span className="material-symbols-outlined text-[17px]" aria-hidden="true">
                                {doc.is_active ? 'toggle_on' : 'toggle_off'}
                              </span>
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditDoc(doc)}
                              title="수정"
                              className="p-1.5 rounded-lg text-stone-400 hover:text-emerald-600 hover:bg-stone-100 transition-colors"
                              aria-label="수정"
                            >
                              <span className="material-symbols-outlined text-[17px]" aria-hidden="true">edit</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleteDocTarget(doc)}
                              title="삭제"
                              className="p-1.5 rounded-lg text-stone-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                              aria-label="삭제"
                            >
                              <span className="material-symbols-outlined text-[17px]" aria-hidden="true">delete</span>
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {/* Table footer */}
              <div className="border-t border-stone-50 px-4 py-2.5 flex items-center justify-between">
                <p className="text-xs text-stone-400">
                  총 <span className="font-semibold text-stone-600">{visibleDocs.length}</span>건
                </p>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ──────────────── MODALS ──────────────── */}
      {catFormTarget && (
        <CategoryFormModal
          category={catFormTarget === 'new' ? null : catFormTarget}
          onClose={() => setCatFormTarget(null)}
        />
      )}

      {deleteCatTarget && (
        <DeleteCategoryModal
          category={deleteCatTarget}
          onClose={() => setDeleteCatTarget(null)}
        />
      )}

      {(isCreatingDoc || editDoc) && (
        <DocFormModal
          doc={editDoc}
          defaultCategoryId={selected.type === 'category' ? selected.id : null}
          categories={activeCategories}
          onClose={() => { setIsCreatingDoc(false); setEditDoc(null); }}
        />
      )}

      {deleteDocTarget && (
        <DeleteDocModal
          doc={deleteDocTarget}
          onClose={() => setDeleteDocTarget(null)}
          onConfirm={handleDeleteDoc}
          isPending={isDeleting}
        />
      )}
    </div>
  );
}
