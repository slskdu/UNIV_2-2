// 1. 상태 관리를 위한 useState를 불러옵니다.
import React, { useState } from 'react'
// 2~3. 다른 파일에서 미리 만들어둔 빈 창고(Context) 두 개를 불러옵니다.
import { UserContext } from './UserContext';
import { ThemeContext } from './ThemeContext';

// 5. [초핵심] 컴포넌트 사이로 들어오는 모든 내용물을 뜻하는 '{children}' 프롭스를 받습니다.
// RootPage에서 <CommonProvider> <Page/> </CommonProvider> 라고 적었기 때문에, 
// 여기서 children은 <Page/> 컴포넌트 전체를 의미하게 됩니다.
const CommonProvider = ({children}) => {
    // 6. 다크모드 상태를 관리합니다. (초기값을 안 적으면 undefined가 되며, false처럼 작동합니다.)
    const [dark, setDark] = useState();
    
    // 7~10. 로그인 상태와 사용자 이름을 하나의 객체로 묶어서 관리합니다.
    const [user, setUser] = useState({
        isLogin: false,
        name: '사용자'
    });

    return (
        // 13. [초핵심] 불러온 UserContext의 '제공자(Provider)' 역할을 합니다.
        // value 속성 안에 하위 컴포넌트들에게 공유할 데이터(user, setUser)를 객체 형태로 담아줍니다.
        <UserContext.Provider value={{user, setUser}}>
            
            {/* 14. ThemeContext의 제공자(Provider)를 중첩해서 엽니다. 
                마찬가지로 value 안에 공유할 데이터(dark, setDark)를 담아줍니다. */}
            <ThemeContext.Provider value={{dark, setDark}}>
                
                {/* 15. 이 창고(Provider)들 안에 자식 컴포넌트들(children, 즉 <Page/>)을 렌더링합니다.
                    이제 이 안에 있는 모든 하위 컴포넌트들은 언제든 창고에서 데이터를 꺼내 쓸 수 있습니다! */}
                {children}
                
            </ThemeContext.Provider>
        </UserContext.Provider>
    )
}
export default CommonProvider