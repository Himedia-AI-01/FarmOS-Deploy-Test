/**
 * parseOrderFlowMessage 단위 테스트
 *
 * 각 테스트의 입력 문자열은 백엔드 order_graph/prompts.py 의
 * ORDER_PROMPTS 리터럴을 그대로 렌더링한 결과입니다.
 *
 * 앵커 주석 형식:
 *   // ANCHOR: <prompts.py 키> — "<분기 판단에 사용되는 리터럴>"
 *
 * prompts.py 를 수정할 때 이 앵커가 깨지면 테스트가 실패하므로,
 * 프론트엔드 파서도 함께 수정해야 함을 알 수 있습니다.
 */

import { describe, it, expect } from 'vitest';
import { parseOrderFlowMessage } from './parseOrderFlowMessage';

// ── 헬퍼 ──────────────────────────────────────────────────────────────────────

/** 단일 주문 항목 블록 (노드가 생성하는 order_list 행 형식) */
function makeOrderBlock(num: number, orderId: number, product: string, date: string): string {
  return `${num}) 주문 번호 #${orderId}\n· 상품: ${product}\n· 주문일: ${date}`;
}

// ── cs-handoff ───────────────────────────────────────────────────────────────

describe('cs-handoff', () => {
  it('CS 핸드오프 메시지를 cs-handoff 로 파싱한다', () => {
    // ANCHOR: cs/prompts.py CS_OUTPUT_PROMPT — "교환과 반품·환불 중 원하시는 처리 방법"
    const text =
      '불편을 드려서 정말 죄송합니다.\n\n' +
      '교환과 반품·환불 중 원하시는 처리 방법을 알려주세요.\n\n' +
      '1. 교환 — 동일 상품으로 교체\n' +
      '2. 반품·환불 — 반품 후 환불 처리';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'cs-handoff' });
  });

  it('cs-handoff 가 다른 패턴보다 우선한다', () => {
    // cs-handoff 메시지에 "(네 / 아니오)" 가 혼재하더라도 cs-handoff 가 먼저 매칭
    const text =
      '교환과 반품·환불 중 원하시는 처리 방법을 알려주세요.\n진행하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'cs-handoff' });
  });
});

// ── confirm ──────────────────────────────────────────────────────────────────

describe('confirm', () => {
  it('cancel_summary 렌더링을 confirm 으로 파싱한다', () => {
    // ANCHOR: cancel_summary — "(네 / 아니오)"
    const text =
      '아래 내용으로 취소 접수를 진행합니다.\n\n' +
      '주문: #100 (딸기 2kg)\n' +
      '취소 사유: 단순 변심\n' +
      '환불 방법: 원결제 수단 환불\n\n' +
      '진행하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'confirm' });
  });

  it('exchange_summary 렌더링을 confirm 으로 파싱한다', () => {
    // ANCHOR: exchange_summary — "(네 / 아니오)"
    const text =
      '아래 내용으로 교환 접수를 진행합니다.\n\n' +
      '주문: #100 (딸기 2kg)\n' +
      '교환 상품: 딸기 2kg × 1\n' +
      '교환 사유: 상품 불량\n\n' +
      '진행하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'confirm' });
  });

  it('duplicate_ticket 중복 안내를 confirm 으로 파싱한다', () => {
    // ANCHOR: order_graph/prompts.py duplicate_ticket — "(네 / 아니오)"
    const text =
      '이미 동일한 교환 접수 티켓(#42)이 처리 대기 중입니다.\n' +
      '이전에 접수하신 내용을 새로 입력하신 내용으로 수정하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'confirm' });
  });

  it('stock_insufficient 렌더링을 confirm 으로 파싱한다', () => {
    // ANCHOR: stock_insufficient — "(네 / 아니오)"
    const text =
      '요청하신 상품의 재고가 부족합니다.\n\n' +
      '딸기 2kg: 요청 3개 / 재고 1개\n\n' +
      '가능한 수량으로 교환 진행하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'confirm' });
  });
});

// ── order-select ─────────────────────────────────────────────────────────────

describe('order-select', () => {
  it('select_order_cancel 렌더링에서 단일 주문을 파싱한다', () => {
    // ANCHOR: select_order_cancel — "번호(1, 2 …)를 입력해 주세요"
    const orderBlock = makeOrderBlock(1, 100, '딸기 2kg', '2025-01-10');
    const text =
      '취소 가능한 주문 목록입니다.\n\n' +
      `${orderBlock}\n\n` +
      '취소하실 주문의 번호(1, 2 …)를 입력해 주세요.\n' +
      "진행을 중단하려면 '그만'이라고 입력하세요.";

    const result = parseOrderFlowMessage(text);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('order-select');
    if (result?.type !== 'order-select') return;

    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toEqual({
      num: '1',
      orderId: '#100',
      summary: '딸기 2kg',
      date: '2025-01-10',
    });
  });

  it('select_order_exchange 렌더링에서 복수 주문을 파싱한다', () => {
    // ANCHOR: select_order_exchange — "번호(1, 2 …)를 입력해 주세요"
    const block1 = makeOrderBlock(1, 200, '사과 1kg, 배 2개', '2025-02-01');
    const block2 = makeOrderBlock(2, 201, '감 3kg', '2025-02-05');
    const text =
      '교환 가능한 주문 목록입니다.\n\n' +
      `${block1}\n\n${block2}\n\n` +
      '교환하실 주문의 번호(1, 2 …)를 입력해 주세요.\n' +
      "진행을 중단하려면 '그만'이라고 입력하세요.";

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('order-select');
    if (result?.type !== 'order-select') return;

    expect(result.items).toHaveLength(2);
    expect(result.items[0]).toMatchObject({ num: '1', orderId: '#200' });
    expect(result.items[1]).toMatchObject({ num: '2', orderId: '#201' });
  });

  it('select_order_change 렌더링에서 주문 변경 목록을 파싱한다', () => {
    // ANCHOR: select_order_change — "번호(1, 2 …)를 입력해 주세요"
    const orderBlock = makeOrderBlock(1, 210, '나주배 선물세트', '2025-02-10');
    const text =
      '변경 가능한 주문 목록입니다.\n\n' +
      `${orderBlock}\n\n` +
      '변경하실 주문의 번호(1, 2 …)를 입력해 주세요.\n' +
      "진행을 중단하려면 '그만'이라고 입력하세요.";

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('order-select');
    if (result?.type !== 'order-select') return;

    expect(result.items[0]).toMatchObject({ num: '1', orderId: '#210' });
  });

  it('invalid_order_selection 재선택 프롬프트도 파싱한다', () => {
    // ANCHOR: invalid_order_selection — "번호(1, 2 …)를 입력해 주세요"
    const orderBlock = makeOrderBlock(1, 300, '복숭아 1kg', '2025-03-01');
    const text =
      '입력하신 번호를 확인할 수 없습니다. 다시 선택해 주세요.\n\n' +
      `${orderBlock}\n\n` +
      '번호(1, 2 …)를 입력해 주세요.';

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('order-select');
    if (result?.type !== 'order-select') return;
    expect(result.items[0]).toMatchObject({ num: '1', orderId: '#300' });
  });

  it('summary 없는 주문 항목(상품·날짜 행 누락)을 빈 문자열로 채운다', () => {
    const text =
      '취소 가능한 주문 목록입니다.\n\n' +
      '1) 주문 번호 #999\n\n' +
      '취소하실 주문의 번호(1, 2 …)를 입력해 주세요.\n' +
      "진행을 중단하려면 '그만'이라고 입력하세요.";

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('order-select');
    if (result?.type !== 'order-select') return;
    expect(result.items[0]).toEqual({ num: '1', orderId: '#999', summary: '', date: '' });
  });

  it('order_list 가 비어 있으면(블록 없음) null 을 반환한다', () => {
    // order_list placeholder 가 채워지지 않은 엣지케이스 방어
    const text =
      '취소 가능한 주문 목록입니다.\n\n' +
      '\n\n' +
      '취소하실 주문의 번호(1, 2 …)를 입력해 주세요.\n' +
      "진행을 중단하려면 '그만'이라고 입력하세요.";

    expect(parseOrderFlowMessage(text)).toBeNull();
  });
});

// ── item-select ───────────────────────────────────────────────────────────────

describe('item-select', () => {
  it('select_items 렌더링에서 상품 목록을 파싱한다', () => {
    // ANCHOR: select_items — "교환할 상품과 수량을 알려주세요"
    const text =
      '교환할 상품과 수량을 알려주세요.\n\n' +
      '주문: #100 (딸기 2kg, 사과 1kg)\n' +
      '상품 목록:\n' +
      '1. 딸기 2kg × 3\n' +
      '2. 사과 1kg × 2\n\n' +
      "예) '1번 상품 2개' 또는 '딸기 2kg 전체'";

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('item-select');
    if (result?.type !== 'item-select') return;

    expect(result.items).toHaveLength(2);
    expect(result.items[0]).toEqual({ num: '1', label: '딸기 2kg × 3' });
    expect(result.items[1]).toEqual({ num: '2', label: '사과 1kg × 2' });
  });

  it('상품 목록 행이 없어도 type: item-select 를 반환한다 (빈 items)', () => {
    // 품목이 없는 비정상 케이스에서도 type 이 잘못 결정되지 않도록 확인
    const text =
      '교환할 상품과 수량을 알려주세요.\n\n' +
      '주문: #100\n' +
      '상품 목록:\n\n' +
      "예) '1번 상품 2개' 또는 '딸기 2kg 전체'";

    const result = parseOrderFlowMessage(text);
    expect(result).toEqual({ type: 'item-select', items: [] });
  });
});

// ── simple-options ────────────────────────────────────────────────────────────

describe('simple-options', () => {
  it('cancel_reason 렌더링에서 옵션을 파싱한다 (기타 포함)', () => {
    // ANCHOR: cancel_reason — "번호 또는 사유를 입력해 주세요"
    const text =
      '취소 사유를 선택해 주세요.\n\n' +
      '1. 단순 변심\n' +
      '2. 상품 불량\n' +
      '3. 배송 지연\n' +
      '4. 오배송\n' +
      '5. 기타 (직접 입력)\n\n' +
      '번호 또는 사유를 입력해 주세요.';

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('simple-options');
    if (result?.type !== 'simple-options') return;

    expect(result.items).toHaveLength(5);
    expect(result.items[0]).toEqual({ num: '1', label: '단순 변심', isOther: false });
    expect(result.items[4]).toEqual({ num: '5', label: '기타 (직접 입력)', isOther: true });
  });

  it('exchange_reason 렌더링에서 옵션을 파싱한다', () => {
    // ANCHOR: exchange_reason — "번호 또는 사유를 입력해 주세요"
    const text =
      '교환 사유를 선택해 주세요.\n\n' +
      '1. 상품 불량\n' +
      '2. 오배송\n' +
      '3. 단순 변심\n' +
      '4. 기타 (직접 입력)\n\n' +
      '번호 또는 사유를 입력해 주세요.';

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('simple-options');
    if (result?.type !== 'simple-options') return;

    expect(result.items).toHaveLength(4);
    expect(result.items[3]).toMatchObject({ isOther: true });
  });

  it('refund_method 렌더링에서 옵션을 파싱한다', () => {
    // ANCHOR: refund_method — "번호를 입력해 주세요"
    const text =
      '환불 방법을 선택해 주세요.\n\n' +
      '1. 원결제 수단 환불 (카드/계좌이체 등)\n' +
      '2. 적립금으로 환불\n\n' +
      '번호를 입력해 주세요.';

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('simple-options');
    if (result?.type !== 'simple-options') return;

    expect(result.items).toHaveLength(2);
    expect(result.items[0]).toEqual({ num: '1', label: '원결제 수단 환불 (카드/계좌이체 등)', isOther: false });
    expect(result.items[1]).toEqual({ num: '2', label: '적립금으로 환불', isOther: false });
  });

  it('change_type 렌더링에서 변경 유형 옵션을 파싱한다', () => {
    // ANCHOR: change_type — "번호를 입력해 주세요"
    const text =
      '어떤 내용을 변경하시겠어요?\n\n' +
      '1. 배송지 변경\n' +
      '2. 연락처 변경\n' +
      '3. 배송 요청사항 변경\n' +
      '4. 상품 수량 변경\n' +
      '5. 기타 변경\n\n' +
      '번호를 입력해 주세요.';

    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('simple-options');
    if (result?.type !== 'simple-options') return;

    expect(result.items).toHaveLength(5);
    expect(result.items[0]).toMatchObject({ num: '1', label: '배송지 변경' });
  });

  it('기타로 시작하지 않는 항목은 isOther: false 다', () => {
    const text = '1. 상품 불량\n2. 오배송\n\n번호 또는 사유를 입력해 주세요.';
    const result = parseOrderFlowMessage(text);
    expect(result?.type).toBe('simple-options');
    if (result?.type !== 'simple-options') return;
    expect(result.items.every((i) => !i.isOther)).toBe(true);
  });
});

describe('editable-input', () => {
  it('change_detail 렌더링에서 현재 등록값을 기본값으로 파싱한다', () => {
    const text =
      '배송지 변경에 필요한 내용을 확인하고 수정해 주세요.\n\n' +
      '현재 등록된 내용:\n' +
      '서울시 강남구 기존 주소\n\n' +
      '새 배송지의 받는 분, 연락처, 주소를 입력해 주세요.\n\n' +
      '수정할 내용을 확인한 뒤 전송해 주세요.';

    const result = parseOrderFlowMessage(text);

    expect(result?.type).toBe('editable-input');
    if (result?.type !== 'editable-input') return;
    expect(result.input.defaultValue).toBe('서울시 강남구 기존 주소');
    expect(result.input.guide).toBe('새 배송지의 받는 분, 연락처, 주소를 입력해 주세요.');
  });
});

// ── null 반환 케이스 ──────────────────────────────────────────────────────────

describe('null 반환 (인터랙티브 UI 없음)', () => {
  it('빈 문자열 → null', () => {
    expect(parseOrderFlowMessage('')).toBeNull();
  });

  it('ticket_created 완료 메시지 → null', () => {
    // ANCHOR: ticket_created — 확인 앵커 없음
    const text =
      '접수가 완료되었습니다.\n\n' +
      '티켓 번호: **#42**\n' +
      '처리 현황은 마이페이지에서 확인하실 수 있습니다.\n' +
      '추가 문의 사항이 있으시면 언제든지 말씀해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('flow_cancelled 취소 메시지 → null', () => {
    // ANCHOR: flow_cancelled — 확인 앵커 없음
    const text = '접수가 취소되었습니다. 다시 진행하려면 언제든지 말씀해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('no_cancellable_orders 안내 메시지 → null', () => {
    // ANCHOR: no_cancellable_orders — 확인 앵커 없음
    const text =
      '현재 취소 가능한 주문이 없습니다.\n' +
      '취소는 배송사 픽업 전(결제 완료·배송 준비 중) 단계에서만 가능합니다.\n' +
      '이미 배송이 시작된 경우 수령 후 교환·반품으로 접수해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('ticket_modified 수정 완료 메시지 → null', () => {
    const text =
      '접수 내용이 수정되었습니다.\n\n' +
      '티켓 번호: **#42**\n' +
      '처리 현황은 마이페이지에서 확인하실 수 있습니다.\n' +
      '추가 문의 사항이 있으시면 언제든지 말씀해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('ticket_unchanged 유지 메시지 → null', () => {
    const text =
      '기존 접수 내용(#42)이 유지됩니다.\n' +
      '처리 현황은 마이페이지에서 확인하실 수 있습니다.\n' +
      '추가 문의 사항이 있으시면 언제든지 말씀해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('no_exchangeable_orders 안내 메시지 → null', () => {
    const text =
      '현재 교환 가능한 주문이 없습니다.\n' +
      '교환은 배송 완료된 주문에 한해 접수할 수 있습니다.\n' +
      '배송 중인 상품은 수령 후 다시 문의해 주세요.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });

  it('일반 CS 답변 메시지 → null', () => {
    const text = '딸기 2kg 상품은 냉장 3~5일 보관이 가능합니다.';
    expect(parseOrderFlowMessage(text)).toBeNull();
  });
});

// ── 우선순위·중복 매칭 방어 ───────────────────────────────────────────────────

describe('우선순위', () => {
  it('(네 / 아니오) 가 다른 패턴과 함께 있으면 confirm 이 우선한다', () => {
    // 이론상 발생하지 않지만 방어적으로 확인
    const text =
      '번호(1, 2 …)를 입력해 주세요.\n' +
      '진행하시겠습니까? (네 / 아니오)';
    expect(parseOrderFlowMessage(text)).toEqual({ type: 'confirm' });
  });
});
