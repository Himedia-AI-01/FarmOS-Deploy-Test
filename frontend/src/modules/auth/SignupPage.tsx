import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';
import { API_BASE } from '@/utils/api';

export default function SignupPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    user_id: '',
    name: '',
    email: '',
    password: '',
    password_confirm: '',
    area: '',
    farmname: '',
  });
  const [loading, setLoading] = useState(false);
  const [postcode, setPostcode] = useState('');
  const [roadAddress, setRoadAddress] = useState('');
  const [detailAddress, setDetailAddress] = useState('');

  useEffect(() => {
    if (document.getElementById('daum-postcode-script')) return;
    const script = document.createElement('script');
    script.id = 'daum-postcode-script';
    script.src = '//t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js';
    script.async = true;
    document.head.appendChild(script);
  }, []);

  const update = (field: string, value: string) => setForm(prev => ({ ...prev, [field]: value }));

  const handleAddressSearch = () => {
    if (typeof daum === 'undefined') {
      toast.error('주소 검색 서비스를 불러올 수 없습니다.');
      return;
    }
    new daum.Postcode({
      oncomplete(data) {
        setPostcode(data.zonecode);
        setRoadAddress(data.roadAddress);
        setDetailAddress('');
      },
    }).open();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.user_id || !form.name || !form.email || !form.password) {
      toast.error('필수 항목을 모두 입력해주세요.');
      return;
    }
    if (form.password !== form.password_confirm) {
      toast.error('비밀번호가 일치하지 않습니다.');
      return;
    }
    if (form.password.length < 4) {
      toast.error('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    setLoading(true);
    try {
      const { password_confirm, area, ...rest } = form;
      const finalLocation = detailAddress
        ? `${roadAddress} ${detailAddress}`.trim()
        : roadAddress;
      const body = { ...rest, location: finalLocation, area: area ? parseFloat(area) : 0 };
      const res = await fetch(`${API_BASE}/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '회원가입에 실패했습니다.');
      }
      toast.success('회원가입이 완료되었습니다! 로그인해주세요.');
      navigate('/login');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '회원가입에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const inputClass = 'w-full px-4 py-3 text-lg border border-gray-300 rounded-xl focus:ring-2 focus:ring-primary focus:border-primary outline-none transition';

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md"
      >
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">회원가입</h1>
          <p className="text-gray-500 mt-1">FarmOS 2.0에 가입하세요</p>
        </div>

        <div className="card">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">아이디 <span className="text-danger">*</span></label>
              <input type="text" value={form.user_id} onChange={e => update('user_id', e.target.value)} placeholder="4~10자" className={inputClass} />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">이름 <span className="text-danger">*</span></label>
              <input type="text" value={form.name} onChange={e => update('name', e.target.value)} placeholder="이름" className={inputClass} />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">이메일 <span className="text-danger">*</span></label>
              <input type="email" value={form.email} onChange={e => update('email', e.target.value)} placeholder="email@example.com" className={inputClass} />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">비밀번호 <span className="text-danger">*</span></label>
              <input type="password" value={form.password} onChange={e => update('password', e.target.value)} placeholder="4자 이상" className={inputClass} />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">비밀번호 확인 <span className="text-danger">*</span></label>
              <input type="password" value={form.password_confirm} onChange={e => update('password_confirm', e.target.value)} placeholder="비밀번호 재입력" className={inputClass} />
            </div>

            <hr className="border-gray-200" />

            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">농장 이름</label>
              <input type="text" value={form.farmname} onChange={e => update('farmname', e.target.value)} placeholder="선택 사항" className={inputClass} />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">주소</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={postcode}
                  readOnly
                  placeholder="우편번호"
                  className={`${inputClass} bg-gray-100 cursor-not-allowed flex-1`}
                />
                <button
                  type="button"
                  onClick={handleAddressSearch}
                  className="px-4 py-3 bg-primary text-white rounded-xl font-semibold whitespace-nowrap hover:opacity-90 transition"
                >
                  우편번호 찾기
                </button>
              </div>
              <input
                type="text"
                value={roadAddress}
                readOnly
                placeholder="도로명주소"
                className={`${inputClass} bg-gray-100 cursor-not-allowed mt-2`}
              />
              <input
                type="text"
                value={detailAddress}
                onChange={e => setDetailAddress(e.target.value)}
                placeholder="상세주소 입력"
                className={`${inputClass} mt-2`}
              />
            </div>
            <div>
              <label className="block text-base font-medium text-gray-700 mb-1">면적 (평)</label>
              <input type="number" step="0.1" value={form.area} onChange={e => update('area', e.target.value)} placeholder="예: 33.2" className={inputClass} />
            </div>

            <button type="submit" disabled={loading} className="btn-primary w-full mt-2">
              {loading ? '가입 중...' : '회원가입'}
            </button>
          </form>

          <div className="mt-4 text-center">
            <Link to="/login" className="text-gray-500 hover:text-primary transition text-base">
              이미 계정이 있으신가요? <span className="text-primary font-semibold">로그인</span>
            </Link>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
