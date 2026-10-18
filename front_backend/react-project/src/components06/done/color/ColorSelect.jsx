// 1. Context 창고에서 데이터를 꺼내오기 위해 useContext 훅을 불러옵니다.
import React, { useContext } from 'react'
import { ColorContext } from './ColorContext';

const ColorSelect = () => {
    // 5. 화면에 그릴 7가지 무지개 색상 문자열을 배열로 미리 만들어 둡니다.
    const colors = ['red', 'orange', 'yellow', 'green', 'blue', 'indigo', 'violet'];
    
    // 6. [초핵심] ColorContext 창고에 접근해서 색상을 변경하는 '함수' 두 개(setBig, setSmall)만 꺼내옵니다.
    // (여기서는 현재 색상이 무엇인지 조회할 필요는 없고 오직 '변경'만 하면 되기 때문입니다.)
    const { setBig, setSmall } = useContext(ColorContext);

    // 8. 마우스 왼쪽 버튼 클릭 시 실행할 함수입니다.
    const onClickLeft = (color) => {
        setBig(color); // 넘겨받은 색상으로 큰 박스의 상태를 업데이트합니다.
    }

    // 12. [초핵심] 마우스 오른쪽 버튼 클릭 시 실행할 함수입니다.
    const onClickRight = (e, color) => {
        // 13. 우클릭 시 원래 브라우저에서 뜨는 '새 탭에서 열기, 복사' 등의 기본 메뉴창이 뜨지 않도록 완벽히 막아줍니다!
        e.preventDefault(); 
        setSmall(color); // 넘겨받은 색상으로 작은 박스의 상태를 업데이트합니다.
    }

    return (
        <div>
            <h2>색상을 선택하세요.</h2>
            {/* 19. 버튼들을 가로로 나란히 배치하기 위해 CSS flex를 인라인 스타일로 적용했습니다. */}
            <div style={{ display:'flex' }}>
                
                {/* 20. [초핵심] 배열의 map() 함수를 이용해 7개의 색상 박스를 반복해서 그립니다. */}
                { colors.map(color =>
                    // 21~22. 
                    // - onContextMenu: 마우스 우클릭 이벤트입니다. 이벤트 객체(e)와 현재 반복 중인 색상(color)을 넘겨줍니다.
                    // - onClick: 마우스 좌클릭 이벤트입니다.
                    // - key={color}: map으로 반복 생성되는 태그에는 반드시 고유한 key를 넣어주어야 에러가 나지 않습니다.
                    <div onContextMenu={(e)=>onClickRight(e, color)} onClick={()=>onClickLeft(color)} 
                        key={color} style={{background:color, width:'30px', height:'30px', cursor:'pointer'}}/>
                )}
            </div>
        </div>
    )
}

export default ColorSelect