import { useState, useMemo } from 'react';
import toast from 'react-hot-toast';
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
  useFaqTopCited,
  useFaqActionSummary,
  useFaqRecommendations,
  useGenerateFaqDraft,
  usePolicyArticles,
} from '@/admin/hooks/useFaqDocs';
import type {
  FaqCategory,
  FaqCategoryCreate,
  FaqCategoryUpdate,
  FaqDoc,
  FaqDocCreate,
  FaqDocUpdate,
  FaqRecommendationItem,
  PolicyArticleItem,
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

const POLICY_DOCS = [
  '반품교환환불정책',
  '배송정책',
  '주문및결제정책',
  '개인정보처리및회원정책',
  '고객서비스운영정책',
  '상품품질신선도보증정책',
] as const;

const AI_TIPS = [
  {
    icon: 'manage_search',
    title: '검색 최적화',
    desc: '제목을 사용자가 실제로 묻는 질문 형태로 작성하세요. 챗봇이 의도를 더 정확히 매칭합니다.',
  },
  {
    icon: 'format_quote',
    title: '인용 가능한 구조',
    desc: '답변은 2~4문장으로 명확하게 작성하세요. 정책 근거가 있으면 (근거: ...) 형식으로 반드시 첨부하세요.',
  },
  {
    icon: 'category',
    title: '카테고리 지정',
    desc: '카테고리를 지정하면 챗봇이 관련 FAQ를 좁혀서 검색해 정확도가 높아집니다.',
  },
  {
    icon: 'edit_note',
    title: '답변 길이',
    desc: '너무 짧으면 정보가 부족하고, 너무 길면 챗봇이 핵심을 추출하기 어렵습니다. 100~300자를 권장합니다.',
  },
] as const;

interface DocFormModalProps {
  doc: FaqDoc | null;
  defaultCategoryId?: number | null;
  defaultTitle?: string;
  defaultContent?: string;
  defaultCitationDoc?: string;
  defaultCitationChapter?: string;
  defaultCitationArticle?: string;
  defaultCitationClause?: string;
  isAiDraft?: boolean;
  categories: FaqCategory[];
  onClose: () => void;
}

function DocFormModal({ doc, defaultCategoryId, defaultTitle, defaultContent, defaultCitationDoc, defaultCitationChapter, defaultCitationArticle, defaultCitationClause, isAiDraft, categories, onClose }: DocFormModalProps) {
  const isEdit = doc != null;

  const [categoryId, setCategoryId] = useState<number | null>(
    doc?.faq_category_id ?? defaultCategoryId ?? null,
  );
  const [title, setTitle] = useState(doc?.title ?? defaultTitle ?? '');
  const [content, setContent] = useState(doc?.content ?? defaultContent ?? '');

  // 정책 인용 필드 — AI 초안에서 pre-fill 가능
  const [citationDoc, setCitationDoc] = useState(
    (doc?.extra_metadata?.citation_doc as string) ?? defaultCitationDoc ?? '',
  );
  const [citationChapter, setCitationChapter] = useState(
    (doc?.extra_metadata?.citation_chapter as string) ?? defaultCitationChapter ?? '',
  );
  const [citationArticle, setCitationArticle] = useState(
    (doc?.extra_metadata?.citation_article as string) ?? defaultCitationArticle ?? '',
  );
  const [citationClause, setCitationClause] = useState(
    (doc?.extra_metadata?.citation_clause as string) ?? defaultCitationClause ?? '',
  );

  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const { data: articleData } = usePolicyArticles(citationDoc);
  const allArticleOptions: PolicyArticleItem[] = articleData ?? [];

  // Unique chapters in order (empty string = uncategorized, shown without chapter label)
  const chapterOptions: string[] = Array.from(
    new Map(allArticleOptions.map(a => [a.chapter, true])).keys()
  );

  // Articles filtered by selected chapter (if chapter selected)
  const articleOptions: PolicyArticleItem[] = citationChapter
    ? allArticleOptions.filter(a => a.chapter === citationChapter)
    : allArticleOptions;

  // Clauses for the selected article
  const clauseOptions: string[] = allArticleOptions.find(a => a.article === citationArticle)?.clauses ?? [];

  const { mutate: create, isPending: isCreating } = useCreateFaqDoc();
  const { mutate: update, isPending: isUpdating } = useUpdateFaqDoc();
  const isPending = isCreating || isUpdating;

  // 인용 미리보기 문자열
  const citationPreview = citationDoc
    ? `(근거: ${citationDoc}${citationChapter ? ' ' + citationChapter : ''}${citationArticle ? ' ' + citationArticle : ''}${citationClause ? ' ' + citationClause : ''})`
    : '';

  // extra_metadata 빌드
  function buildExtraMeta(): Record<string, unknown> | null {
    if (!citationDoc) return null;
    return {
      citation_doc: citationDoc,
      ...(citationChapter && { citation_chapter: citationChapter }),
      ...(citationArticle && { citation_article: citationArticle }),
      ...(citationClause && { citation_clause: citationClause }),
    };
  }

  // 정책 인용이 있으면 content 말미에 자동 삽입
  function buildFinalContent(): string {
    const base = content.trim();
    const withoutOld = base.replace(/\(근거:[^)]*\)/g, '').trimEnd();
    if (!citationPreview) return withoutOld;
    return `${withoutOld}\n${citationPreview}`;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;

    const finalContent = buildFinalContent();
    const extra = buildExtraMeta();

    if (isEdit) {
      const payload: FaqDocUpdate = {
        faq_category_id: categoryId ?? null,
        title: title.trim(),
        content: finalContent,
        extra_metadata: extra,
      };
      update(
        { id: doc.id, payload },
        {
          onSuccess: () => {
            setFeedback({ type: 'success', msg: '수정됐습니다.' });
            setTimeout(onClose, 900);
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
        content: finalContent,
        ...(extra && { extra_metadata: extra }),
      };
      create(payload, {
        onSuccess: () => {
          setFeedback({ type: 'success', msg: '등록됐습니다.' });
          setTimeout(onClose, 900);
        },
        onError: () => {
          setFeedback({ type: 'error', msg: '등록에 실패했습니다.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      });
    }
  }

  const faqIdLabel = isEdit ? `#FAQ-${String(doc.id).padStart(4, '0')}` : null;
  const wordCount = content.trim().length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? 'FAQ 문서 수정' : 'FAQ 문서 등록'}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col">

        {/* ── 헤더 ── */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-100">
          <div className="flex items-center gap-3">
            <span className="material-symbols-outlined text-[20px] text-emerald-600" aria-hidden="true">
              {isEdit ? 'edit_document' : 'post_add'}
            </span>
            <div>
              <h2 className="text-sm font-bold text-stone-900 leading-tight">
                {isEdit ? 'FAQ 수정' : 'FAQ 등록'}
              </h2>
              {faqIdLabel && (
                <p className="text-[11px] text-stone-400 font-mono mt-0.5">{faqIdLabel}</p>
              )}
            </div>
            {!categoryId && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-600 border border-amber-200">
                <span className="material-symbols-outlined text-[12px]" aria-hidden="true">warning</span>
                카테고리 미지정
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-stone-400 hover:bg-stone-100 transition-colors"
            aria-label="닫기"
          >
            <span className="material-symbols-outlined text-[20px]" aria-hidden="true">close</span>
          </button>
        </div>

        {/* ── 본문 (2-column) ── */}
        <div className="flex flex-1 overflow-hidden">

          {/* LEFT: 콘텐츠 편집 */}
          <form
            id="doc-form"
            onSubmit={handleSubmit}
            className="flex-1 overflow-y-auto px-6 py-5 space-y-5 border-r border-stone-100"
          >
            {feedback && (
              <div
                className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium ${
                  feedback.type === 'success'
                    ? 'bg-emerald-50 border border-emerald-200 text-emerald-700'
                    : 'bg-rose-50 border border-rose-200 text-rose-700'
                }`}
                role="status"
              >
                <span
                  className="material-symbols-outlined text-[16px]"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                  aria-hidden="true"
                >
                  {feedback.type === 'success' ? 'check_circle' : 'error'}
                </span>
                {feedback.msg}
              </div>
            )}

            {/* 카테고리 */}
            <div>
              <label className="block text-xs font-semibold text-stone-500 mb-2 uppercase tracking-wide">
                카테고리
              </label>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => setCategoryId(null)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border-2 transition-all ${
                    categoryId === null
                      ? 'border-stone-400 bg-stone-100 text-stone-700'
                      : 'border-stone-200 text-stone-400 hover:border-stone-300'
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
                    <span
                      className="material-symbols-outlined text-[13px]"
                      style={{ fontVariationSettings: "'FILL' 1" }}
                      aria-hidden="true"
                    >
                      {cat.icon}
                    </span>
                    {cat.name}
                  </button>
                ))}
              </div>
            </div>

            {/* 질문 */}
            <div>
              <label htmlFor="doc-title" className="block text-xs font-semibold text-stone-500 mb-1.5 uppercase tracking-wide">
                질문 제목 <span className="text-rose-400">*</span>
              </label>
              <input
                id="doc-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                maxLength={500}
                placeholder="예: 배송은 얼마나 걸리나요?"
                className="w-full border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 placeholder:text-stone-300 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
              />
              <p className="text-[11px] text-stone-400 text-right mt-1">{title.length} / 500</p>
            </div>

            {/* 답변 */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label htmlFor="doc-content" className="text-xs font-semibold text-stone-500 uppercase tracking-wide flex items-center gap-1.5">
                  답변 본문 <span className="text-rose-400">*</span>
                  {isAiDraft && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-600 normal-case tracking-normal">
                      AI 초안 · 검토 후 등록하세요
                    </span>
                  )}
                </label>
                <span className={`text-[11px] font-medium ${
                  wordCount < 30 ? 'text-rose-400' :
                  wordCount > 400 ? 'text-amber-500' :
                  'text-emerald-600'
                }`}>
                  {wordCount}자
                  {wordCount < 30 && ' · 너무 짧아요'}
                  {wordCount > 400 && ' · 줄여보세요'}
                </span>
              </div>
              <textarea
                id="doc-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                required
                rows={10}
                placeholder="예: 평균 2~3일 내 배송됩니다. 산간 지역은 추가 1~2일이 소요될 수 있습니다."
                className="w-full border border-stone-200 rounded-xl px-4 py-3 text-sm text-stone-900 placeholder:text-stone-300 leading-relaxed outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all resize-y"
              />
            </div>

            {/* 인용 미리보기 (content 말미에 삽입될 텍스트) */}
            {citationPreview && (
              <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-3">
                <p className="text-[11px] font-semibold text-emerald-600 mb-1 uppercase tracking-wide">저장 시 답변 말미에 자동 삽입</p>
                <p className="text-xs text-emerald-800 font-mono">{citationPreview}</p>
              </div>
            )}
          </form>

          {/* RIGHT: 인용 & AI 팁 */}
          <div className="w-72 shrink-0 overflow-y-auto px-5 py-5 space-y-5 bg-stone-50/60">

            {/* 편집 모드: 인용 현황 */}
            {isEdit && (
              <div className="bg-white border border-stone-100 rounded-2xl p-4 shadow-sm">
                <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wide mb-3">챗봇 인용 현황</p>
                <div className="flex items-end gap-2">
                  <span className="text-2xl font-bold text-emerald-700">{doc.citation_count}</span>
                  <span className="text-xs text-stone-400 mb-0.5">회 인용됨</span>
                </div>
                <div className={`mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                  doc.citation_count >= 10
                    ? 'bg-emerald-50 text-emerald-700'
                    : doc.citation_count >= 3
                    ? 'bg-amber-50 text-amber-600'
                    : 'bg-rose-50 text-rose-600'
                }`}>
                  <span className="material-symbols-outlined text-[12px]" aria-hidden="true">
                    {doc.citation_count >= 10 ? 'trending_up' : doc.citation_count >= 3 ? 'trending_flat' : 'trending_down'}
                  </span>
                  {doc.citation_count >= 10 ? '자주 인용됨' : doc.citation_count >= 3 ? '보통' : '인용 저조'}
                </div>
              </div>
            )}

            {/* 정책 인용 */}
            <div className="bg-white border border-stone-100 rounded-2xl p-4 shadow-sm space-y-3">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[16px] text-emerald-600" aria-hidden="true">gavel</span>
                <p className="text-[11px] font-semibold text-stone-700 uppercase tracking-wide">정책 인용</p>
              </div>

              {/* 문서 선택 */}
              <div>
                <label htmlFor="citation-doc" className="block text-[11px] font-medium text-stone-500 mb-1">정책 문서</label>
                <select
                  id="citation-doc"
                  value={citationDoc}
                  onChange={(e) => {
                    setCitationDoc(e.target.value);
                    setCitationChapter('');
                    setCitationArticle('');
                    setCitationClause('');
                  }}
                  className="w-full border border-stone-200 rounded-lg px-3 py-2 text-xs text-stone-900 bg-white outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 transition-all"
                >
                  <option value="">선택 안 함</option>
                  {POLICY_DOCS.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>

              {/* 장 */}
              <div>
                <label htmlFor="citation-chapter" className="block text-[11px] font-medium text-stone-500 mb-1">
                  장 <span className="font-normal text-stone-400">(예: 제1장 반품·교환·환불)</span>
                </label>
                <select
                  id="citation-chapter"
                  value={citationChapter}
                  onChange={(e) => {
                    setCitationChapter(e.target.value);
                    setCitationArticle('');
                    setCitationClause('');
                  }}
                  disabled={!citationDoc || chapterOptions.length === 0}
                  className="w-full border border-stone-200 rounded-lg px-3 py-2 text-xs text-stone-900 bg-white outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-40 transition-all"
                >
                  <option value="">선택 안 함</option>
                  {chapterOptions.map((chapter) => (
                    <option key={chapter} value={chapter}>{chapter || '(장 없음)'}</option>
                  ))}
                </select>
              </div>

              {/* 조 */}
              <div>
                <label htmlFor="citation-article" className="block text-[11px] font-medium text-stone-500 mb-1">
                  조 <span className="font-normal text-stone-400">(예: 제5조(반품 조건))</span>
                </label>
                <select
                  id="citation-article"
                  value={citationArticle}
                  onChange={(e) => {
                    setCitationArticle(e.target.value);
                    setCitationClause('');
                  }}
                  disabled={!citationDoc}
                  className="w-full border border-stone-200 rounded-lg px-3 py-2 text-xs text-stone-900 bg-white outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-40 transition-all"
                >
                  <option value="">선택 안 함</option>
                  {articleOptions.map((item) => (
                    <option key={item.article} value={item.article}>{item.article}</option>
                  ))}
                </select>
              </div>

              {/* 항 */}
              <div>
                <label htmlFor="citation-clause" className="block text-[11px] font-medium text-stone-500 mb-1">
                  항 <span className="font-normal text-stone-400">(예: 제1항)</span>
                </label>
                <select
                  id="citation-clause"
                  value={citationClause}
                  onChange={(e) => setCitationClause(e.target.value)}
                  disabled={!citationArticle || clauseOptions.length === 0}
                  className="w-full border border-stone-200 rounded-lg px-3 py-2 text-xs text-stone-900 bg-white outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-40 transition-all"
                >
                  <option value="">선택 안 함</option>
                  {clauseOptions.map((clause) => (
                    <option key={clause} value={clause}>{clause}</option>
                  ))}
                </select>
              </div>

              {/* 인용 미리보기 */}
              {citationPreview ? (
                <div className="bg-emerald-50 border border-emerald-100 rounded-lg px-3 py-2">
                  <p className="text-[10px] font-semibold text-emerald-500 mb-0.5">미리보기</p>
                  <p className="text-[11px] text-emerald-800 font-mono leading-relaxed">{citationPreview}</p>
                </div>
              ) : (
                <p className="text-[11px] text-stone-400 italic">정책 문서를 선택하면 인용구가 생성됩니다.</p>
              )}
            </div>

            {/* AI 작성 팁 */}
            <div className="bg-emerald-700 rounded-2xl p-4 text-white">
              <div className="flex items-center gap-2 mb-3">
                <span className="material-symbols-outlined text-[16px] text-emerald-200" aria-hidden="true">auto_awesome</span>
                <p className="text-[11px] font-semibold text-emerald-100 uppercase tracking-wide">AI 작성 팁</p>
              </div>
              <ul className="space-y-3">
                {AI_TIPS.map((tip) => (
                  <li key={tip.icon} className="flex gap-2.5">
                    <span
                      className="material-symbols-outlined text-[15px] text-emerald-300 mt-0.5 shrink-0"
                      aria-hidden="true"
                    >
                      {tip.icon}
                    </span>
                    <div>
                      <p className="text-[11px] font-semibold text-white">{tip.title}</p>
                      <p className="text-[11px] text-emerald-200 mt-0.5 leading-relaxed">{tip.desc}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

          </div>
        </div>

        {/* ── 푸터 ── */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-stone-100 bg-stone-50/50 rounded-b-2xl">
          <p className="text-[11px] text-stone-400">
            {isEdit ? `마지막 수정: ${doc.updated_at.slice(0, 10)}` : '새 FAQ를 등록합니다'}
          </p>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-5 py-2 rounded-xl text-sm font-semibold text-stone-500 hover:bg-stone-100 transition-colors"
            >
              취소
            </button>
            <button
              type="submit"
              form="doc-form"
              disabled={isPending || !title.trim() || !content.trim()}
              className="inline-flex items-center gap-2 px-6 py-2 rounded-xl text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors shadow-sm"
            >
              {isPending ? (
                <>
                  <span className="material-symbols-outlined text-[16px] animate-spin" aria-hidden="true">progress_activity</span>
                  {isEdit ? '저장 중...' : '등록 중...'}
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-[16px]" aria-hidden="true">
                    {isEdit ? 'save' : 'add_circle'}
                  </span>
                  {isEdit ? '저장' : '등록'}
                </>
              )}
            </button>
          </div>
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
// BentoCards — 4개 요약 카드
// ──────────────────────────────────────────

interface BentoCardsProps {
  onConvertToFaq: (item: FaqRecommendationItem) => void;
  isDraftGenerating: boolean;
  generatingForRank: number | null;
}

function BentoCards({ onConvertToFaq, isDraftGenerating, generatingForRank }: BentoCardsProps) {
  const { data: summary } = useFaqActionSummary();
  const { data: topCited = [] } = useFaqTopCited(3);
  const { data: recommendations } = useFaqRecommendations(30, 3);

  const totalDocs = summary?.total_docs ?? 0;
  const activeDocs = summary?.active_docs ?? 0;
  const underperformingCount = summary?.underperforming_count ?? 0;
  const activeRatio = totalDocs > 0 ? Math.round((activeDocs / totalDocs) * 100) : 0;

  const recItems = recommendations?.items ?? [];

  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 px-6 pt-5 pb-3">

      {/* Card 1 — Total FAQs */}
      <div className="bg-white border border-stone-100 rounded-2xl p-4 shadow-sm flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-emerald-50 flex items-center justify-center">
              <span
                className="material-symbols-outlined text-emerald-600 text-[18px]"
                style={{ fontVariationSettings: "'FILL' 1" }}
                aria-hidden="true"
              >
                library_books
              </span>
            </div>
            <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">Total FAQs</span>
          </div>
          <span className="text-[11px] font-semibold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
            활성 {activeDocs}개
          </span>
        </div>
        <div className="flex items-end justify-between">
          <span className="text-3xl font-extrabold text-stone-800 tabular-nums leading-none">
            {totalDocs.toLocaleString()}
          </span>
        </div>
        {/* Active ratio progress bar */}
        <div className="space-y-1">
          <div className="w-full h-1.5 bg-stone-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${activeRatio}%` }}
            />
          </div>
          <p className="text-[11px] text-stone-400 tabular-nums">{activeRatio}% 활성화됨</p>
        </div>
      </div>

      {/* Card 2 — Top Cited */}
      <div className="bg-white border border-stone-100 rounded-2xl p-4 shadow-sm flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-amber-50 flex items-center justify-center">
            <span
              className="material-symbols-outlined text-amber-500 text-[18px]"
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden="true"
            >
              trending_up
            </span>
          </div>
          <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">Top Cited</span>
        </div>
        {topCited.length === 0 ? (
          <p className="text-xs text-stone-300 flex-1 flex items-center">아직 인용 데이터가 없습니다.</p>
        ) : (
          <ol className="space-y-2 flex-1">
            {topCited.map((item, idx) => (
              <li key={item.id} className="flex items-center gap-2">
                <span className="text-[11px] font-bold text-stone-300 tabular-nums w-3 shrink-0">{idx + 1}</span>
                <span className="text-xs text-stone-600 flex-1 truncate leading-snug">
                  {item.title.length > 25 ? `${item.title.slice(0, 25)}…` : item.title}
                </span>
                <span className="text-[11px] font-bold text-amber-600 tabular-nums shrink-0">
                  {item.citation_count.toLocaleString()}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* Card 3 — Underperforming */}
      <div className="bg-white border-t-2 border-t-rose-400 border border-stone-100 rounded-2xl p-4 shadow-sm flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-rose-50 flex items-center justify-center">
            <span
              className="material-symbols-outlined text-rose-500 text-[18px]"
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden="true"
            >
              warning
            </span>
          </div>
          <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">인용 저조</span>
        </div>
        <div className="flex-1">
          <p className="text-3xl font-extrabold text-stone-800 tabular-nums leading-none">
            {underperformingCount}
          </p>
          <p className="text-xs text-stone-400 mt-1">인용 0회 FAQ</p>
        </div>
      </div>

      {/* Card 4 — FAQ 등록 추천 (emerald bg) */}
      <div className="bg-emerald-700 rounded-2xl p-4 shadow-sm flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-emerald-600 flex items-center justify-center">
            <span
              className="material-symbols-outlined text-white text-[18px]"
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden="true"
            >
              auto_awesome
            </span>
          </div>
          <span className="text-[11px] font-bold text-emerald-200 uppercase tracking-wide">FAQ 등록 추천</span>
        </div>

        {recItems.length === 0 ? (
          <p className="text-xs text-emerald-300 flex-1 flex items-center">추천 데이터가 없습니다.</p>
        ) : (
          <ol className="space-y-2 flex-1">
            {recItems.map((item) => {
              const isThisGenerating = isDraftGenerating && generatingForRank === item.rank;
              return (
                <li key={item.rank} className="flex items-start gap-2 group">
                  <span className="text-[11px] font-bold text-emerald-400 w-3 shrink-0 mt-0.5 tabular-nums">{item.rank}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      {/* 갭 유형 뱃지 */}
                      {item.gap_type === 'escalated' ? (
                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-rose-500/80 text-white leading-none shrink-0">
                          처리불가
                        </span>
                      ) : (
                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-amber-400/80 text-white leading-none shrink-0">
                          누락FAQ
                        </span>
                      )}
                      <span className="text-[10px] text-emerald-400 truncate">{item.top_intent_label}</span>
                      <span className="text-[11px] font-bold text-white tabular-nums ml-auto shrink-0">{item.count}건</span>
                    </div>
                    <div className="flex items-center justify-between gap-1 mt-0.5">
                      <p className="text-[11px] text-emerald-100 truncate leading-snug flex-1">
                        {item.representative_question.length > 24
                          ? `${item.representative_question.slice(0, 24)}…`
                          : item.representative_question}
                      </p>
                      <button
                        type="button"
                        onClick={() => onConvertToFaq(item)}
                        disabled={isDraftGenerating}
                        className="shrink-0 flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-white/20 hover:bg-white/30 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        aria-label={`${item.rank}위 추천 질문으로 AI FAQ 초안 생성`}
                      >
                        {isThisGenerating ? (
                          <>
                            <span className="material-symbols-outlined text-[11px] animate-spin" aria-hidden="true">progress_activity</span>
                            생성 중
                          </>
                        ) : (
                          <>
                            <span className="material-symbols-outlined text-[11px]" aria-hidden="true">auto_awesome</span>
                            AI 초안
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
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
  const [newDocPrefillTitle, setNewDocPrefillTitle] = useState<string | undefined>(undefined);
  const [newDocPrefillContent, setNewDocPrefillContent] = useState<string | undefined>(undefined);
  const [newDocPrefillCategoryId, setNewDocPrefillCategoryId] = useState<number | null | undefined>(undefined);
  const [newDocPrefillCitationDoc, setNewDocPrefillCitationDoc] = useState<string | undefined>(undefined);
  const [newDocPrefillCitationChapter, setNewDocPrefillCitationChapter] = useState<string | undefined>(undefined);
  const [newDocPrefillCitationArticle, setNewDocPrefillCitationArticle] = useState<string | undefined>(undefined);
  const [newDocPrefillCitationClause, setNewDocPrefillCitationClause] = useState<string | undefined>(undefined);
  const [newDocIsAiDraft, setNewDocIsAiDraft] = useState(false);
  const [isDraftGenerating, setIsDraftGenerating] = useState(false);
  const [generatingForRank, setGeneratingForRank] = useState<number | null>(null);
  const [deleteDocTarget, setDeleteDocTarget] = useState<FaqDoc | null>(null);
  const [docListError, setDocListError] = useState<string | null>(null);

  // ── Data ──
  const { data: categories = [] } = useFaqCategories(true);
  const { data: summary } = useFaqActionSummary();
  const { mutateAsync: generateDraft } = useGenerateFaqDraft();

  // ── AI 초안 생성 핸들러 ──
  async function handleConvertToFaq(item: FaqRecommendationItem) {
    setIsDraftGenerating(true);
    setGeneratingForRank(item.rank);
    const toastId = toast.loading('AI가 FAQ 초안을 작성 중입니다...');
    try {
      const draft = await generateDraft({
        representative_question: item.representative_question,
        top_intent: item.top_intent,
        gap_type: item.gap_type,
        count: item.count,
        escalated_count: item.escalated_count,
      });
      toast.dismiss(toastId);
      toast.success('AI 초안이 생성되었습니다. 내용을 검토 후 등록하세요.');
      setNewDocPrefillTitle(draft.title);
      setNewDocPrefillContent(draft.content);
      setNewDocPrefillCategoryId(draft.suggested_category_id ?? null);
      setNewDocPrefillCitationDoc(draft.citation_doc ?? undefined);
      setNewDocPrefillCitationChapter(draft.citation_chapter ?? undefined);
      setNewDocPrefillCitationArticle(draft.citation_article ?? undefined);
      setNewDocPrefillCitationClause(draft.citation_clause ?? undefined);
      setNewDocIsAiDraft(true);
    } catch {
      toast.dismiss(toastId);
      toast.error('초안 생성에 실패했습니다. 직접 작성해주세요.');
      setNewDocPrefillTitle(item.representative_question);
      setNewDocPrefillContent(undefined);
      setNewDocPrefillCategoryId(undefined);
      setNewDocPrefillCitationDoc(undefined);
      setNewDocPrefillCitationChapter(undefined);
      setNewDocPrefillCitationArticle(undefined);
      setNewDocPrefillCitationClause(undefined);
      setNewDocIsAiDraft(false);
    } finally {
      setIsDraftGenerating(false);
      setGeneratingForRank(null);
      setIsCreatingDoc(true);
    }
  }

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
      filtered = [...filtered].sort((a, b) => a.citation_count - b.citation_count);
    }
    return filtered;
  }, [allDocs, selected, searchQuery, sortKey]);

  // ── Relative citation ratio for current page ──
  const maxCitationInView = useMemo(
    () => Math.max(0, ...visibleDocs.map((d) => d.citation_count)),
    [visibleDocs],
  );

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

  function getCitationBarColor(count: number): string {
    if (count >= 10) return 'bg-emerald-500';
    if (count === 0) return 'bg-rose-400';
    return 'bg-stone-300';
  }

  function getCitationTextColor(count: number): string {
    if (count >= 10) return 'text-emerald-600';
    if (count === 0) return 'text-rose-400';
    return 'text-stone-500';
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

        {/* ── Bento Cards ── */}
        <BentoCards
          onConvertToFaq={handleConvertToFaq}
          isDraftGenerating={isDraftGenerating}
          generatingForRank={generatingForRank}
        />

        {/* ── Toolbar ── */}
        <div className="bg-white border-b border-stone-100 px-6 py-3 flex items-center gap-3 flex-wrap">
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
            <option value="citations">인용 적은 순</option>
          </select>

          <div className="ml-auto">
            <button
              type="button"
              onClick={() => setIsCreatingDoc(true)}
              className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 text-white text-sm font-semibold rounded-xl hover:bg-emerald-700 transition-colors shadow-sm"
            >
              <span className="material-symbols-outlined text-[17px]" aria-hidden="true">add</span>
              FAQ 등록
            </button>
          </div>
        </div>

        {/* Doc operation error banner */}
        {docListError && (
          <div className="mx-6 mt-4 px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700 font-medium" role="alert">
            {docListError}
          </div>
        )}

        {/* ── Table ── */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {/* Knowledge Base Directory header */}
          <div className="flex items-center justify-between px-4 py-2.5 mb-2 bg-stone-50/80 border border-stone-100 rounded-xl">
            <span className="text-xs font-bold text-stone-600"></span>
            <span className="text-xs text-stone-400">
              활성 FAQ:{' '}
              <span className="font-semibold text-stone-600 tabular-nums">
                {summary?.active_docs ?? totalActive}개
              </span>
            </span>
          </div>

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
                    <th className="px-4 py-3 text-left text-xs font-semibold text-stone-500 w-28">ID</th>
                    <th className="px-3 py-3 text-left text-xs font-semibold text-stone-500 w-28">카테고리</th>
                    <th className="px-3 py-3 text-left text-xs font-semibold text-stone-500">질문</th>
                    <th className="px-3 py-3 text-center text-xs font-semibold text-stone-500 w-20">인용 수</th>
                    <th className="px-3 py-3 text-left text-xs font-semibold text-stone-500 w-36">상대 비율</th>
                    <th className="px-3 py-3 text-center text-xs font-semibold text-stone-500 w-24">수정일</th>
                    <th className="px-3 py-3 w-24" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-50">
                  {visibleDocs.map((doc) => {
                    const citationRatio = maxCitationInView > 0
                      ? Math.round((doc.citation_count / maxCitationInView) * 100)
                      : 0;

                    return (
                      <tr
                        key={doc.id}
                        className={`group transition-colors hover:bg-stone-50 ${!doc.is_active ? 'opacity-40' : ''}`}
                      >
                        {/* ID */}
                        <td className="px-4 py-3">
                          <span className="text-xs font-mono text-stone-400">
                            #{String(doc.id).padStart(4, '0')}
                          </span>
                        </td>

                        {/* Category */}
                        <td className="px-3 py-3">{getCategoryChip(doc)}</td>

                        {/* Question (title) */}
                        <td className="px-3 py-3 max-w-sm">
                          <span className="block truncate font-medium text-stone-800 text-sm">{doc.title}</span>
                        </td>

                        {/* Citation count */}
                        <td className="px-3 py-3 text-center">
                          <span className={`text-xs font-semibold tabular-nums ${getCitationTextColor(doc.citation_count)}`}>
                            {doc.citation_count}회
                          </span>
                        </td>

                        {/* Relative ratio bar */}
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-24 h-1.5 bg-stone-100 rounded-full overflow-hidden shrink-0">
                              <div
                                className={`h-full rounded-full transition-all duration-300 ${getCitationBarColor(doc.citation_count)}`}
                                style={{ width: `${citationRatio}%` }}
                              />
                            </div>
                            <span className={`text-xs font-bold tabular-nums ${getCitationTextColor(doc.citation_count)}`}>
                              {citationRatio}%
                            </span>
                          </div>
                        </td>

                        {/* Updated date */}
                        <td className="px-3 py-3 text-center text-xs text-stone-400 whitespace-nowrap">
                          {formatDate(doc.updated_at)}
                        </td>

                        {/* Actions */}
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
                  {summary && (
                    <span className="ml-1 text-stone-300">/ {summary.total_docs.toLocaleString()} FAQs</span>
                  )}
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
          defaultCategoryId={editDoc ? undefined : (newDocPrefillCategoryId ?? (selected.type === 'category' ? selected.id : null))}
          defaultTitle={editDoc ? undefined : newDocPrefillTitle}
          defaultContent={editDoc ? undefined : newDocPrefillContent}
          defaultCitationDoc={editDoc ? undefined : newDocPrefillCitationDoc}
          defaultCitationChapter={editDoc ? undefined : newDocPrefillCitationChapter}
          defaultCitationArticle={editDoc ? undefined : newDocPrefillCitationArticle}
          defaultCitationClause={editDoc ? undefined : newDocPrefillCitationClause}
          isAiDraft={!editDoc && newDocIsAiDraft}
          categories={activeCategories}
          onClose={() => {
            setIsCreatingDoc(false);
            setEditDoc(null);
            setNewDocPrefillTitle(undefined);
            setNewDocPrefillContent(undefined);
            setNewDocPrefillCategoryId(undefined);
            setNewDocPrefillCitationDoc(undefined);
            setNewDocPrefillCitationChapter(undefined);
            setNewDocPrefillCitationArticle(undefined);
            setNewDocPrefillCitationClause(undefined);
            setNewDocIsAiDraft(false);
          }}
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
