-- SPDX-License-Identifier: MIT
-- One {{СбМатчи}} call contains every match; no per-row templates are needed.
local p = {}
local months = {'января','февраля','марта','апреля','мая','июня',
    'июля','августа','сентября','октября','ноября','декабря'}
local fields = {'дата','соперник','счёт','голы','соревнование','источник','пенальти'}
local indexedFields = {'матч','дата','соперник','счёт','голы','соревнование','источник','пенальти'}

local function trim(value)
    return mw.text.trim(tostring(value or ''))
end

local function fail(message)
    error('СбМатчи: ' .. message, 0)
end

local function number(value, name)
    local text = trim(value)
    if not text:match('^%d+$') then fail('неверное число в ' .. name) end
    local n = tonumber(text)
    if n > 1000 then fail('слишком большое число в ' .. name) end
    return n
end

local function parseDate(value)
    local text = trim(value)
    local y,m,d = text:match('^(%d%d%d%d)%-(%d%d)%-(%d%d)$')
    if not y then fail('дата должна иметь вид ГГГГ-ММ-ДД: ' .. text) end
    y,m,d = tonumber(y),tonumber(m),tonumber(d)
    local leap = y%4==0 and (y%100~=0 or y%400==0)
    local days = {31,leap and 29 or 28,31,30,31,30,31,31,30,31,30,31}
    if y<1872 or y>2100 or not days[m] or d<1 or d>days[m] then
        fail('несуществующая дата: ' .. text)
    end
    return text, d .. ' ' .. months[m] .. ' ' .. y
end

local function score(value, name)
    local a,b = trim(value):match('^(%d+)%s*:%s*(%d+)$')
    if not a then fail('счёт должен иметь вид 3:2 в ' .. name) end
    return number(a,name),number(b,name)
end

local function plural(n, one, few, many)
    if n%100>=11 and n%100<=14 then return many end
    if n%10==1 then return one end
    if n%10>=2 and n%10<=4 then return few end
    return many
end

-- Verified English articles; redirects to another age group are excluded.
local countries = {
    ["Ирландия"]={gen="Ирландии", foreign={[0]="Republic of Ireland national football team", [17]="Republic of Ireland national under-17 football team", [19]="Republic of Ireland national under-19 football team", [21]="Republic of Ireland national under-21 football team", [23]="Republic of Ireland national under-23 football team"}},
    ["Нидерланды"]={gen="Нидерландов", foreign={[0]="Netherlands national football team", [17]="Netherlands national under-17 football team", [19]="Netherlands national under-19 football team", [21]="Netherlands national under-21 football team"}},
    ["Сербия"]={gen="Сербии", foreign={[0]="Serbia national football team", [17]="Serbia national under-17 football team", [19]="Serbia national under-19 football team", [20]="Serbia national under-20 football team", [21]="Serbia national under-21 football team"}},
    ["Франция"]={gen="Франции", foreign={[0]="France national football team", [16]="France national under-16 football team", [17]="France national under-17 football team", [18]="France national under-18 football team", [19]="France national under-19 football team", [20]="France national under-20 football team", [21]="France national under-21 football team"}},
    ["Австрия"]={gen="Австрии", foreign={[0]="Austria national football team", [17]="Austria national under-17 football team", [18]="Austria national under-18 football team", [19]="Austria national under-19 football team", [21]="Austria national under-21 football team"}},
    ["Италия"]={gen="Италии", foreign={[0]="Italy national football team", [16]="Italy national under-16 football team", [17]="Italy national under-17 football team", [18]="Italy national under-18 football team", [19]="Italy national under-19 football team", [20]="Italy national under-20 football team", [21]="Italy national under-21 football team", [23]="Italy national under-23 football team"}},
    ["Германия"]={gen="Германии", foreign={[0]="Germany national football team", [16]="Germany national under-16 football team", [17]="Germany national under-17 football team", [20]="Germany national under-20 football team", [21]="Germany national under-21 football team"}},
    ["Швейцария"]={gen="Швейцарии", foreign={[0]="Switzerland national football team", [16]="Switzerland national under-16 football team", [17]="Switzerland national under-17 football team", [18]="Switzerland national under-18 football team", [19]="Switzerland national under-19 football team", [20]="Switzerland national under-20 football team", [21]="Switzerland national under-21 football team", [23]="Switzerland national under-23 football team"}},
    ["Кипр"]={gen="Кипра", foreign={[0]="Cyprus national football team", [17]="Cyprus national under-17 football team", [18]="Cyprus national under-18 football team", [19]="Cyprus national under-19 football team", [21]="Cyprus national under-21 football team"}},
    ["Дания"]={gen="Дании", foreign={[0]="Denmark national football team", [17]="Denmark national under-17 football team", [19]="Denmark national under-19 football team", [21]="Denmark national under-21 football team"}},
    ["Англия"]={gen="Англии", foreign={[0]="England national football team", [16]="England national under-16 football team", [17]="England national under-17 football team", [18]="England national under-18 football team", [19]="England national under-19 football team", [20]="England national under-20 football team", [21]="England national under-21 football team"}},
    ["Испания"]={gen="Испании", foreign={[0]="Spain national football team", [16]="Spain national under-16 football team", [17]="Spain national under-17 football team", [18]="Spain national under-18 football team", [19]="Spain national under-19 football team", [20]="Spain national under-20 football team", [21]="Spain national under-21 football team", [23]="Spain national under-23 football team"}},
    ["Португалия"]={gen="Португалии", foreign={[0]="Portugal national football team", [16]="Portugal national under-16 football team", [17]="Portugal national under-17 football team", [18]="Portugal national under-18 football team", [19]="Portugal national under-19 football team", [20]="Portugal national under-20 football team", [21]="Portugal national under-21 football team"}},
    ["Бельгия"]={gen="Бельгии", foreign={[0]="Belgium national football team", [17]="Belgium national under-17 football team", [18]="Belgium national under-18 football team", [19]="Belgium national under-19 football team", [21]="Belgium national under-21 football team"}},
    ["Россия"]={gen="России", foreign={[0]="Russia national football team", [17]="Russia national under-17 football team", [19]="Russia national under-19 football team", [20]="Russia national under-20 football team", [21]="Russia national under-21 football team"}},
    ["Украина"]={gen="Украины", foreign={[0]="Ukraine national football team", [16]="Ukraine national under-16 football team", [17]="Ukraine national under-17 football team", [18]="Ukraine national under-18 football team", [19]="Ukraine national under-19 football team", [20]="Ukraine national under-20 football team", [21]="Ukraine national under-21 football team"}},
}

local function ageCategory(value)
    local text = trim(value)
    if text=='' then return 0 end
    if not text:match('^%d%d$') then fail('возраст должен быть числом от 15 до 23') end
    local age = tonumber(text)
    if age<15 or age>23 then fail('возраст должен быть числом от 15 до 23') end
    return age
end

local function opponent(value, age, frame, cache)
    -- Explicit wikitext is an editor override, including historical flags.
    if value:find('[[',1,true) or value:find('{{',1,true) then return value end
    local country = countries[value]
    if not country then
        if age>0 then fail('неизвестная сборная «'..value..'»: укажите полную ссылку') end
        return value
    end
    if cache[value] then return cache[value] end
    local article = 'Сборная '..country.gen..' по футболу'
    local label = value
    if age>0 then
        article=article..' (до '..age..' лет)'
        label=label..' (до '..age..' лет)'
    end
    local title = mw.title.new(article)
    local link = '[['..article..'|'..label..']]'
    if (not title or not title.exists or title.isRedirect) and country.foreign[age] then
        link=frame:expandTemplate{title='нп5',args={article,label,'en',country.foreign[age]}}
    elseif title and title.isRedirect then
        -- A redirect may lead to a different age group; do not silently follow it.
        link=label
    end
    local flag=frame:expandTemplate{title='Флаг '..country.gen,args={'20px'}}
    cache[value]=flag..' '..link
    return cache[value]
end

function p._render(args, frame)
    local last = 0
    local allowed = {['игрок']=true,['сборная']=true,['заголовок']=true,
        ['цвета']=true,['обновлено']=true,['свёрнуто']=true,['источники']=true,
        ['соревнование']=true,['возраст']=true}
    for key,value in pairs(args) do
        key = tostring(key)
        if trim(value)~='' and not allowed[key] then
            local found = false
            for _,field in ipairs(indexedFields) do
                if key:sub(1,#field)==field then
                    local suffix = key:sub(#field+1)
                    if suffix:match('^[1-9]%d*$') then
                        local index = tonumber(suffix)
                        if index>500 then fail('поддерживается не больше 500 матчей') end
                        last = math.max(last,index)
                        found = true
                        break
                    end
                end
            end
            if not found then fail('неизвестный параметр ' .. key) end
        end
    end
    if last==0 then fail('нет строк матчей') end
    local age = ageCategory(args['возраст'])
    local opponentCache = {}
    local title = trim(args['заголовок'])
    if title=='' then
        if trim(args['игрок'])=='' or trim(args['сборная'])=='' then
            fail('задайте игрок и сборная либо готовый заголовок')
        end
        title = 'Матчи ' .. args['игрок'] .. ' за ' .. args['сборная']
    end
    local rows, totals, previous = {}, {games=last,goals=0,wins=0,draws=0,losses=0}, nil
    for i=1,last do
        local row = {}
        for _,field in ipairs(fields) do row[field]=trim(args[field..i]) end
        local compact = trim(args['матч'..i])
        if compact~='' then
            local parts, start = {}, 1
            while true do
                local position = compact:find(';;',start,true)
                parts[#parts+1]=trim(compact:sub(start,position and position-1 or #compact))
                if not position then break end
                start=position+2
            end
            if #parts<4 or #parts>5 then
                fail('матч'..i..': нужны дата ;; соперник ;; счёт ;; голы [;; соревнование]')
            end
            for j,field in ipairs({'дата','соперник','счёт','голы','соревнование'}) do
                if row[field]~='' then fail('не смешивайте матч'..i..' и '..field..i) end
                row[field]=parts[j] or ''
            end
        end
        if row['соревнование']=='' then row['соревнование']=trim(args['соревнование']) end
        for _,field in ipairs({'дата','соперник','счёт','голы','соревнование'}) do
            if row[field]=='' then fail('отсутствует ' .. field .. i) end
        end
        row['соперник']=opponent(row['соперник'],age,frame,opponentCache)
        local iso,display = parseDate(row['дата'])
        if previous and iso<previous then fail('матчи должны идти по датам') end
        previous = iso
        row.date, row.iso = display, iso
        local own,other = score(row['счёт'],'счёт'..i)
        local goals = number(row['голы'],'голы'..i)
        if goals>own then fail('голов игрока больше, чем голов сборной в матче '..i) end
        row.score = own .. ':' .. other
        if row['пенальти']~='' then
            if own~=other then fail('серия пенальти требует ничейного счёта в матче '..i) end
            local pa,pb = score(row['пенальти'],'пенальти'..i)
            if pa==pb then fail('серия пенальти не может завершиться вничью') end
            row.score = row.score .. ' (' .. pa .. ':' .. pb .. ' пен.)'
        end
        totals.goals = totals.goals+goals
        if own>other then totals.wins=totals.wins+1
        elseif own<other then totals.losses=totals.losses+1
        else totals.draws=totals.draws+1 end
        row.goals = goals
        rows[#rows+1]=row
    end
    local updated = trim(args['обновлено'])
    local prefix = ''
    if updated~='' then
        local iso = parseDate(updated)
        if iso<previous then fail('дата обновления раньше последнего матча') end
        prefix = frame:expandTemplate{title='обновлено',args={iso}} .. '\n'
    end
    local collapsed = trim(args['свёрнуто'])
    if collapsed~='' and collapsed~='да' and collapsed~='нет' then
        fail('свёрнуто должно быть да или нет')
    end
    local tab = mw.html.create('table'):addClass('wikitable mw-collapsible')
    if collapsed~='нет' then tab:addClass('mw-collapsed') end
    tab:cssText('text-align:center; font-size:95%; max-width:100%;')
    local heading = tab:tag('tr'):tag('th'):attr('colspan',6)
    local palette = trim(args['цвета'])
    if palette~='' then
        if palette:find('[|{}<>\n\r]') then fail('неверное название палитры сборной') end
        heading:cssText(frame:expandTemplate{title='цвета сборной/'..palette,args={'1'}})
    end
    heading:wikitext(title)
    local columns = tab:tag('tr')
    for _,name in ipairs({'№','Дата','Соперник','Счёт','Голы','Соревнование'}) do
        columns:tag('th'):attr('scope','col'):wikitext(name)
    end
    for i,row in ipairs(rows) do
        local tr = tab:tag('tr')
        for j,value in ipairs({i,row.date,row['соперник'],row.score,row.goals,row['соревнование']..row['источник']}) do
            local cell = tr:tag('td')
            if j==4 then cell:cssText('white-space:nowrap;') end
            cell:wikitext(tostring(value))
        end
    end
    local summary = "'''Итого: " .. totals.games .. ' ' .. plural(totals.games,'матч','матча','матчей')
        .. ' / ' .. totals.goals .. ' ' .. plural(totals.goals,'гол','гола','голов')
        .. '; ' .. totals.wins .. ' ' .. plural(totals.wins,'победа','победы','побед')
        .. ', ' .. totals.draws .. ' ' .. plural(totals.draws,'ничья','ничьи','ничьих')
        .. ', ' .. totals.losses .. ' ' .. plural(totals.losses,'поражение','поражения','поражений') .. "'''"
    return prefix .. tostring(tab) .. '\n\n' .. summary .. trim(args['источники'])
end

function p.main(frame)
    local parent = frame:getParent()
    return p._render(parent and parent.args or frame.args, frame)
end

return p
