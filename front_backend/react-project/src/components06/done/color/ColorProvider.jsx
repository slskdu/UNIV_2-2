// 1. 상태 관리를 위한 useState 훅을 불러옵니다.
import React, { useState } from 'react'
// 2. 미리 만들어둔 빈 창고(Context)를 불러옵니다. (createContext로 만들어진 파일입니다.)
import { ColorContext } from './ColorContext';

// 4. [초핵심] 부모(ColorPage)가 감싸고 있는 하위 컴포넌트들을 통째로 넘겨받는 'children' 프롭스입니다.
const ColorProvider = ({ children }) => {
    // 5. 큰 박스의 배경색 상태입니다. 초기값은 'black'입니다.
    const [big, setBig] = useState('black');
    
    // 6. 작은 박스의 배경색 상태입니다. 초기값은 'small'입니다.
    // (참고: CSS 색상명에 'small'은 없으므로, 아마 교수님께서 타이핑하시다 'gray'나 'white' 대신 실수로 적으신 오타 같습니다! ㅎㅎ 그래도 로직 작동에는 문제없습니다.)
    const [small, setSmall] = useState('small');

    return (
        // 9. [초핵심] ColorContext의 '제공자(Provider)' 역할을 시작합니다.
        // value 속성 안에 하위 컴포넌트들에게 공유할 데이터 4개(big, setBig, small, setSmall)를 모두 객체로 묶어서 담아줍니다.
        <ColorContext.Provider value={{big, setBig, small, setSmall}}>
            
            {/* 10. 이 창고 안에 자식 컴포넌트들(<ColorSelect/>, <ColorBox/>)을 배치하여 렌더링합니다. */}
            {children}
            
        </ColorContext.Provider>
    )
}

export default ColorProvider