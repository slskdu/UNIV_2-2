// 1. Context 창고에서 데이터를 꺼내오기 위해 useContext 훅을 불러옵니다.
import React, { useContext } from 'react'
// 2. 접근할 창고 이름(ColorContext)을 불러옵니다.
import { ColorContext } from './ColorContext'

const ColorBox = () => {
    // 5. [초핵심] ColorContext 창고에 접근해서 큰 박스용 색상(big)과 작은 박스용 색상(small)을 꺼내옵니다.
    // 아직 Provider 코드를 보진 못했지만, 그곳에 { big: '어떤색', small: '어떤색' } 형태로 데이터가 저장되어 있다는 것을 유추할 수 있습니다!
    const { big, small } = useContext(ColorContext);
    
    return (
        <div>
            {/* 8. [초핵심] 리액트의 인라인 스타일 문법입니다.
                - HTML에서는 <div style="width: 100px;"> 처럼 문자열로 썼지만,
                - 리액트에서는 자바스크립트 객체(Object) 형태로 넣어야 합니다.
                - 바깥쪽 { } : "이제부터 자바스크립트 코드를 쓸 거야!"
                - 안쪽 { } : "이것은 자바스크립트 객체(Object)야!"
                - background 값으로 창고에서 꺼내온 'big' 변수를 넣어줍니다. */}
            <div style={{ width:'100px', height:'100px', background:big }}/>
            
            {/* 9. 같은 원리로 50x50 사이즈의 작은 박스를 만들고, 배경색으로 'small' 변수를 넣어줍니다. */}
            <div style={{ width:'50px', height:'50px', background:small }}/>
        </div>
    )
}

export default ColorBox